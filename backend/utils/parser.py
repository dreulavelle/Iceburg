import re
import PTN
from typing import List
from pydantic import BaseModel
from utils.settings import settings_manager


class ParserConfig(BaseModel):
    language: List[str]
    include_4k: bool
    highest_quality: bool
    repack_proper: bool
    dual_audio: bool   # This sometimes doesnt work depending on if other audio is in the title
    av1_audio: bool


class Parser:
    
    def __init__(self):
        self.settings = ParserConfig(**settings_manager.get("parser"))
        self.language = self.settings.language or ["English"]
        self.resolution = ["1080p", "720p"]
        self.unwanted_codec = ["H.263", "Xvid"]  # Bad for transcoding
        self.unwanted_quality = ["Cam", "Telesync", "Telecine", "Screener", 
                                 "DVDSCR", "Workprint", "DVD-Rip", "TVRip", 
                                 "VODRip", "DVD-R", "DSRip", "BRRip"]
        self.quality = [None, "Blu-ray", "WEB-DL", "WEBRip", "HDRip", 
                        "HDTVRip", "BDRip", "Pay-Per-View Rip"]
        self.audio = [None, "AAC", "AAC 2.0", "AAC 5.1", "FLAC", "AVC", "Custom"]
        self.network = ["Apple TV+", "Amazon Studios", "Netflix", 
                        "Nickelodeon", "YouTube Premium", "Disney Plus", 
                        "DisneyNOW", "HBO Max", "HBO", "Hulu Networks", 
                        "DC Universe", "Adult Swim", "Comedy Central", 
                        "Peacock", "AMC", "PBS", "Crunchyroll", 
                        "Syndication", "Hallmark", "BBC", "VICE",
                        "MSNBC", "Crave"]  # Will probably be used later in `Versions`
        self.validate_settings()

    def validate_settings(self):
        if self.settings.highest_quality:
            self.resolution = ["UHD", "2160p", "4K", "1080p", "720p"]
            self.audio += ["Dolby TrueHD", "Dolby Atmos",
                          "Dolby Digital EX", "Dolby Digital Plus",
                          "Dolby Digital 5.1", "Dolby Digital 7.1",
                          "Dolby Digital Plus 5.1", "Dolby Digital Plus 7.1"
                          "DTS-HD MA", "DTS-HD MA", "DTS-HD", "DTS-HD MA 5.1"
                          "DTS-EX", "DTS:X", "DTS", "5.1", "7.1"]
        elif self.settings.include_4k:
            self.resolution = ["2160p", "4K", "1080p", "720p"]
        else:
            self.resolution = ["1080p", "720p"]
        if self.settings.dual_audio:
            self.audio += ["Dual"]
        if not self.settings.av1_audio:
            self.unwanted_codec += ["AV1"]  # Not all devices support this codec

    def _parse(self, string):
        parse = PTN.parse(string)

        # episodes
        episodes = []
        if parse.get("episode", False):
            episode = parse.get("episode")
            if type(episode) == list:
                for sub_episode in episode:
                    episodes.append(int(sub_episode))
            else:
                episodes.append(int(episode))

        title = parse.get("title")
        season = parse.get("season")
        audio = parse.get("audio")
        codec = parse.get("codec")
        resolution = parse.get("resolution")
        quality = parse.get("quality")
        subtitles = parse.get("subtitles")
        language = parse.get("language")
        hdr = parse.get("hdr")
        upscaled = parse.get("upscaled")
        remastered = parse.get("remastered")
        proper = parse.get("proper")
        repack = parse.get("repack")
        remux = parse.get("remux")
        if not language:
            language = "English"
        extended = parse.get("extended")

        return {
            "title": title,
            "resolution": resolution or [],
            "quality": quality or [],
            "season": season,
            "episodes": episodes or [],
            "codec": codec or [],
            "audio": audio or [],
            "hdr": hdr or False,
            "upscaled": upscaled or False,
            "remastered": remastered or False,
            "proper": proper or False,
            "repack": repack or False,
            "subtitles": True if subtitles == "Available" else False,
            "language": language or [],
            "remux": remux or False,
            "extended": extended,
        }

    def episodes(self, string) -> List[int]:
        parse = self._parse(string)
        return parse["episodes"]

    def episodes_in_season(self, season, string) -> List[int]:
        parse = self._parse(string)
        if parse["season"] == season:
            return parse["episodes"]
        return []

    def _is_4k(self, string) -> bool:
        """Check if content is `4k`."""
        if self.settings.include_4k:
            parsed = self._parse(string)
            return parsed.get("resolution", False) in ["2160p", "4K"]

    def _is_highest_quality(self, string) -> bool:
        """Check if content is `highest quality`."""
        if self.settings.highest_quality:
            parsed = self._parse(string)
            return any([
                parsed.get("hdr", False),
                parsed.get("remux", False),
                parsed.get("audio", False) in self.audio,
                parsed.get("resolution", False) in ["UHD", "2160p", "4K"],
                parsed.get("upscaled", False)
            ])

    def _is_repack_or_proper(self, string) -> bool:
        """Check if content is `repack` or `proper`."""
        if self.settings.repack_proper:
            parsed = self._parse(string)
            return any([
                parsed.get("proper", False),
                parsed.get("repack", False),
            ])

    def _is_dual_audio(self, string) -> bool:
        """Check if content is `dual audio`."""
        if self.settings.dual_audio:
            parsed = self._parse(string)
            return parsed.get("audio") == "Dual" or \
                   re.search(r"((dual.audio)|(english|eng)\W+(dub|audio))", string, flags=re.IGNORECASE) is not None

    def _is_network(self, string) -> bool:
        """Check if content is from a `network`."""
        parsed = self._parse(string)
        return parsed.get("network", False) in self.network

    def sort_streams(self, streams: dict) -> dict:
        """Sorts streams based on user preferences."""
        def sorting_key(item):
            _, stream = item
            title = stream['name']
            return (
                self._is_dual_audio(title),
                self._is_repack_or_proper(title),
                self._is_highest_quality(title),
                self._is_4k(title),
                self._is_network(title)
            )
        sorted_streams = sorted(streams.items(), key=sorting_key, reverse=True)
        return dict(sorted_streams)

    def parse(self, string) -> bool:
        """Parse the given string and return True if it matches the user settings."""
        parse = self._parse(string)
        return (
            parse["resolution"] in self.resolution
            and parse["language"] in self.language
            and parse["audio"] in self.audio
            and not parse["quality"] in self.unwanted_quality
            and not parse["codec"] in self.unwanted_codec
        )

    def get_title(self, string) -> str:
        """Get the `title` from the given string."""
        parse = self._parse(string)
        return parse["title"]

parser = Parser()