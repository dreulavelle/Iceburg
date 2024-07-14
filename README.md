<div align="center">
  <a href="https://github.com/rivenmedia/riven">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/dreulavelle/iceberg/main/assets/iceberg-light.png">
      <img alt="Iceberg" src="https://raw.githubusercontent.com/dreulavelle/iceberg/main/assets/iceberg-dark.png">
    </picture>
  </a>
</div>

<div align="center">
  <a href="https://github.com/dreulavelle/iceberg/stargazers"><img alt="GitHub Repo stars" src="https://img.shields.io/github/stars/dreulavelle/iceberg"></a>
  <a href="https://github.com/dreulavelle/iceberg/issues"><img alt="Issues" src="https://img.shields.io/github/issues/dreulavelle/iceberg" /></a>
  <a href="https://github.com/dreulavelle/iceberg/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/dreulavelle/iceberg"></a>
  <a href="https://github.com/dreulavelle/iceberg/graphs/contributors"><img alt="Contributors" src="https://img.shields.io/github/contributors/dreulavelle/iceberg" /></a>
  <a href="https://discord.gg/wDgVdH8vNM"><img alt="Discord" src="https://img.shields.io/badge/Join%20discord-8A2BE2" /></a>
</div>

<div align="center">
  <p>Plex torrent streaming through Real Debrid and 3rd party services like Overseerr, Mdblist, etc.</p>
</div>

Services currently supported:

| Service            | Supported |
| ------------------ | --------- |
| Real Debrid        | ✅        |
| Plex               | ✅        |
| Overseerr          | ✅        |
| Mdblist            | ✅        |
| Trakt              | ✅        |
| Jackett            | ✅        |
| Plex Watchlist RSS | ✅        |
| Torrentio          | ✅        |
| Orionoid           | ✅        |
| Jackett            | ✅        |
| Listrr             | ✅        |

| and more to come!

Check out out [Project Board](https://github.com/users/dreulavelle/projects/2) to stay informed!

Please add feature requests and issues over on our [Issue Tracker](https://github.com/dreulavelle/iceberg/issues) or join our [Discord](https://discord.gg/wDgVdH8vNM) to chat with us!

We are constantly adding features and improvements as we go along and squashing bugs as they arise.

---

## Table of Contents

-   [Table of Contents](#table-of-contents)
-   [ElfHosted](#elfhosted)
-   [Self Hosted](#self-hosted)
    -   [Docker Compose](#docker-compose)
        -   [What is ORIGIN ?](#what-is-origin-)
    -   [Running outside of Docker](#running-outside-of-docker)
        -   [First terminal:](#first-terminal)
        -   [Second terminal:](#second-terminal)
    -   [Symlinking settings](#symlinking-settings)
        -   [Example:](#example)
-   [Development](#development)
    -   [Development without `make`](#development-without-make)
-   [Contributing](#contributing)
-   [License](#license)

---

## ElfHosted

[ElfHosted](https://elfhosted.com) is a geeky [open-source](https://elfhosted.com/open/) PaaS which provides all the "plumbing" (_hosting, security, updates, etc_) for your self-hosted apps.

> [!IMPORTANT]
> Riven is a top-tier app in the [ElfHosted app catalogue](https://elfhosted.com/apps/). 30% of your subscription goes to Riven developers, and the remainder offsets [infrastructure costs](https://elfhosted.com/open/pricing/).

> [!TIP]
> New accounts get $10 free credit, enough for a week's free trial of the [Riven / Plex Infinite Streaming](https://store.elfhosted.com/product/infinite-plex-riven-streaming-bundle) bundle!

(_[ElfHosted Discord](https://discord.elfhosted.com)_)

## Self Hosted

### Docker Compose

Create a `docker-compose.yml` file with the following contents:

```yml
services:
    riven:
        image: spoked/riven:latest
        container_name: riven
        restart: unless-stopped
        environment:
            PUID: "1000"
            PGID: "1000"
            ORIGIN: "http://localhost:3000" # IMP: read below to avoid CORS issues
            BACKEND_URL: http://127.0.0.1:8080 # optional
        ports:
            - "3000:3000"
        volumes:
            - ./data:/riven/data
            - /mnt:/mnt
```

Then run `docker compose up -d` to start the container in the background. You can then access the web interface at `http://localhost:3000` or whatever port and origin you set in the `docker-compose.yml` file.

> [!TIP]
> On first run, Riven creates a `settings.json` file in the `data` directory. You can edit the settings from frontend, or manually edit the file and restart the container or use `.env` or docker-compose environment variables to set the settings (see `.env.example` for reference).

#### What is ORIGIN ?

`ORIGIN` is the URL of the frontend on which you will access it from anywhere. If you are hosting Riven on a vps with IP address `123.45.67.890` then you will need to set the `ORIGIN` to `http://123.45.67.890:3000` (no trailing slash). Similarly, if using a domain name, you will need to set the `ORIGIN` to `http://riven.example.com:3000` (no trailing slash). If you change the port in the `docker-compose.yml` file, you will need to change it in the `ORIGIN` as well.

### Running outside of Docker

To run outside of docker you will need to have node (v18.13+) and python (3.10+) installed. Then clone the repository

```sh
git clone https://github.com/rivenmedia/riven.git && cd riven
```

and open two terminals in the root of the project and run the following commands in each.

#### First terminal:

```sh
cd frontend
npm install
npm run build
ORIGIN=http://localhost:3000 BACKEND_URL=http://127.0.0.1 node build
```

Read above for more info on `ORIGIN`.

#### Second terminal:

```sh
pip install poetry
poetry install --without dev
poetry run python backend/main.py
```

---

### Symlinking settings

`rclone_path` should point to your rclone mount that has your torrents on your host.

`library_path` should point to the location of the mount in plex container

```json
    "symlink": {
        "rclone_path": "/mnt/zurg",
        "library_path": "/mnt/library"
    }
```

Plex libraries that are currently required to have sections:

| Type   | Categories               |
| ------ | ------------------------ |
| Movies | `movies`, `anime_movies` |
| Shows  | `shows`, `anime_shows`   |

> [!NOTE]
> Currently, these Plex library requirements are mandatory. However, we plan to make them customizable in the future to support additional libraries as per user preferences.

---

## Development

Welcome to the development section! Here, you'll find all the necessary steps to set up your development environment and start contributing to the project.

### Prerequisites

Ensure you have the following installed on your system:

-   **Node.js** (v18.13+)
-   **Python** (3.10+)
-   **Poetry** (for Python dependency management)
-   **Docker** (optional, for containerized development)

### Initial Setup

1. **Clone the Repository:**

    ```sh
    git clone https://github.com/rivenmedia/riven.git && cd riven
    ```

2. **Install Backend Dependencies:**

    ```sh
    pip install poetry
    poetry install
    ```

3. **Install Frontend Dependencies:**
    ```sh
    cd frontend
    npm install
    cd ..
    ```

### Using `make` for Development

We provide a `Makefile` to simplify common development tasks. Here are some useful commands:

-   **Initialize the Project:**

    ```sh
    make
    ```

-   **Start the Development Environment:**
    This command stops any previous containers, removes old images, and rebuilds the image using cached layers. Any changes in the code will trigger a rebuild.

    ```sh
    make start
    ```

-   **Restart the Container:**

    ```sh
    make restart
    ```

-   **View Logs:**
    ```sh
    make logs
    ```

### Development without `make`

If you prefer not to use `make` and Docker, you can manually set up the development environment with the following steps:

1. **Start the Backend:**

    ```sh
    poetry run python backend/main.py
    ```

2. **Start the Frontend:**
    ```sh
    cd frontend
    npm run dev
    ```

### Additional Tips

-   **Environment Variables:**
    Ensure you set the `ORIGIN` environment variable to the URL where the frontend will be accessible. For example:

    ```sh
    export ORIGIN=http://localhost:3000
    ```

-   **Code Formatting:**
    We use `Black` for Python and `Prettier` for JavaScript. Make sure to format your code before submitting any changes.

-   **Running Tests:**
    ```sh
    poetry run pytest
    ```

By following these guidelines, you'll be able to set up your development environment smoothly and start contributing to the project. Happy coding!

---

## Contributing

We welcome contributions from the community! To ensure a smooth collaboration, please follow these guidelines:

### Submitting Changes

1. **Open an Issue**: For major changes, start by opening an issue to discuss your proposed modifications. This helps us understand your intentions and provide feedback early in the process.
2. **Pull Requests**: Once your changes are ready, submit a pull request. Ensure your code adheres to our coding standards and passes all tests.

### Code Formatting

-   **Backend**: We use [Black](https://black.readthedocs.io/en/stable/) for code formatting. Run `black` on your code before submitting.
-   **Frontend**: We use [Prettier](https://prettier.io/) for code formatting. Run `prettier` on your code before submitting.
-   **Line Endings**: Use CRLF line endings unless the file is a shell script or another format that requires LF line endings.

### Dependency Management

We use [Poetry](https://python-poetry.org/) for managing dependencies. Poetry simplifies dependency management by automatically handling package versions and resolving conflicts, ensuring consistency across all environments.

#### Setting Up Your Environment

1. **Install Poetry**: If you haven't already, install Poetry using `pip install poetry`.
2. **Install Dependencies**: After cloning the repository, navigate to the project's root directory and run `poetry install`. This command installs all necessary dependencies as defined in the `pyproject.toml` file and creates an isolated virtual environment.
3. **Activate Virtual Environment**: You can activate the virtual environment using `poetry shell` or run commands directly using `poetry run <command>`.

#### Adding or Updating Dependencies

-   **Add a Dependency**: Use `poetry add <package-name>` to add a new dependency.
-   **Update a Dependency**: Use `poetry update <package-name>` to update an existing dependency.

### Running Tests and Linters

Before submitting a pull request, ensure your changes are compatible with the project's dependencies and coding standards. Use the following commands to run tests and linters:

-   **Run Tests**: `poetry run pytest`
-   **Run Linters**: `poetry run ruff check backend` and `poetry run isort --check-only backend`

By following these guidelines, you help us maintain a high-quality codebase and streamline the review process. Thank you for contributing!

---

<a href="https://github.com/dreulavelle/iceberg/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=dreulavelle/iceberg" />
</a>

---

## License

This project is licensed under the GNU GPLv3 License - see the [LICENSE](LICENSE) file for details
