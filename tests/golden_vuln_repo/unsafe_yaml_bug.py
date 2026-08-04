# Golden fixture: yaml.load() with no safe Loader.
# Expected: analyzers.unsafe_yaml_checker fires.

import yaml


def load_config(raw):
    return yaml.load(raw)
