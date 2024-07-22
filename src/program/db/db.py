from sqla_wrapper import Alembic, SQLAlchemy
from program.settings.manager import settings_manager
from utils import data_dir_path

db = SQLAlchemy(settings_manager.settings.database.host)

script_location = data_dir_path / "alembic/"

import os
if not os.path.exists(script_location):
    os.makedirs(script_location)

alembic = Alembic(db, script_location)
alembic.init(script_location)

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

# https://stackoverflow.com/questions/61374525/how-do-i-check-if-alembic-migrations-need-to-be-generated
def need_upgrade_check() -> bool:
    diff = []
    with db.engine.connect() as connection:
        mc = MigrationContext.configure(connection)
        diff = compare_metadata(mc, db.Model.metadata)
    return diff != []

def run_migrations() -> None:
    try:
        if need_upgrade_check():
            alembic.revision("auto-upg")
            alembic.upgrade()
    except:
        alembic.upgrade()