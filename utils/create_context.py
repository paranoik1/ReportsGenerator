import os
from pathlib import Path

project_dir = Path(__file__).parent.parent
context = []

with open(project_dir / ".gitignore") as fp:
    exclude_dirs_files = set(fp.read().splitlines())

for file_or_folder in [".git", "migration", "utils", "sql", "schemas", "frontend", "alembic.ini", "poetry.lock", "pyproject.toml", "output.txt", ".gitignore", "mypy.ini", "models", "notification_service.py"]:
    exclude_dirs_files.add(file_or_folder)


# print("Exclude dirs:", exclude_dirs)
counter = 1
for root, dirs, files in os.walk(project_dir):
    excluded_dirs = exclude_dirs_files & set(dirs)
    for dir in excluded_dirs:
        # print("Exclude dir:", dir)
        dirs.remove(dir)

    excluded_files = exclude_dirs_files & set(files)
    for file in excluded_files:
        files.remove(file)

    for file in files:
        file_path = Path(root) / file

        with open(file_path) as fp:
            file_content = fp.read()

        print(f"\t{counter}) В файле", str(file_path).replace("/home/q/Projects/TouristEquipmentRental/", ""))
        print(file_content)
        # print("\n")
        counter += 1
