import sys
from importlib.metadata import distributions


def test_console_scripts_target_importable_modules():
    installed_distribution = next(
        distribution
        for distribution in distributions()
        if distribution.metadata["Name"] == "warehouse-dsm"
        and str(distribution._path).startswith(sys.prefix)
    )
    scripts = {
        entry.name: entry.value
        for entry in installed_distribution.entry_points
        if entry.group == "console_scripts"
    }

    assert scripts["warehouse-dsm-run"] == "experiments.run:main"
    assert scripts["warehouse-dsm-viz"] == "app:main"
