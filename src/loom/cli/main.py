import argparse
import asyncio
import enum
import pathlib
import sys
import typing

import pydantic
import rich.console
import yaml

from loom import __default_config_path__, __version__
from loom.config import RootConfig

console = rich.console.Console()

async def config_list(config: RootConfig, args: argparse.Namespace) -> int:
    del args
    console.print(yaml.dump(config.model_dump()))
    return 0


async def config_get(config: RootConfig, args: argparse.Namespace) -> int:
    """
    Print the value of a configuration value in the configuration hierarchy.
    Args:
        config (RootConfig): The root configuration object
        args (): The parsed command line arguments

    Returns:
        An integer exit code
    """
    if 'key' not in args:
        return await config_list(config, args)
    path = args.key.split('.')
    value = config.model_dump(mode='json', exclude_unset=True)
    for p in path:
        value = value[p]
    console.print(yaml.dump(value))
    return 0


async def config_set(config: RootConfig, args: argparse.Namespace) -> int:
    """
    Set a configuration value in the configuration hierarchy.
    Args:
        config (RootConfig): The root configuration object
        args (): The parsed command line arguments

    Returns:
        An integer exit code
    """
    if 'key' not in args or 'value' not in args:
        console.print('Please specify both a key and a value')
        return 1
    path = args.key.split('.')
    parent = config
    leaf = path[-1]
    current_path: list[str] = []
    for container in path[:-1]:
        current_path.append(container)
        if not hasattr(parent, container):
            console.print(f'There is no attribute at {".".join(current_path)}')
            return 1
        parent = getattr(parent, container)
    if not hasattr(parent, leaf):
        console.print(f'There is no attribute at {".".join(current_path + [leaf])}')
        return 1
    if issubclass(type(getattr(parent, leaf)), pydantic.BaseModel):
        console.print(
            'You cannot set the value of an entire object. Set a path that resolves to an attribute instead.'
        )
        return 1
    if issubclass(type(getattr(parent, leaf)), enum.Enum):
        if args.value not in list(type(getattr(parent, leaf))):
            console.print(
                f'The value {args.value} is not a valid option for {args.key}'
            )
            return 1
        else:
            setattr(parent, leaf, type(getattr(parent, leaf))(args.value))
    elif isinstance(getattr(parent, leaf), bool):
        setattr(parent, leaf, args.value.lower() == 'true')
    else:
        setattr(parent, leaf, args.value)
    config.save()
    return 0


def _set_nested_value(target: dict, path: list[str], value: typing.Any) -> None:
    current = target
    for part in path[:-1]:
        current = current.setdefault(part, {})
    current[path[-1]] = value


def _collect_overrides(args: argparse.Namespace) -> dict:
    overrides = {}
    for key, value in vars(args).items():
        if not key.startswith('override_') or value is None:
            continue

        _set_nested_value(
            overrides,
            key.removeprefix('override_').split('__'),
            value,
        )

    return overrides

async def main() -> int:
    try:
        parser = argparse.ArgumentParser(f'Loom {__version__}')
        parser.add_argument(
            '--config',
            type=pathlib.Path,
            required=False,
            dest='config_path',
            default=__default_config_path__,
            help=f'Path to the config file, defaults to {__default_config_path__}',
        )
        parser.add_argument(
            '--verbose',
            '-v',
            action='store_true',
            default=False,
            required=False,
            dest='verbose',
            help='Enable verbose output',
        )
        subparsers = parser.add_subparsers(required=True, help='Sub-commands')
        config_parser = subparsers.add_parser('config', help='Configuration commands')
        config_subparser = config_parser.add_subparsers(required=True)
        config_list_parser = config_subparser.add_parser(
            'list', help='List current configuration'
        )
        config_list_parser.set_defaults(func=config_list)
        config_get_parser = config_subparser.add_parser(
            'get', help='Get a configuration value'
        )
        config_get_parser.add_argument('key', help='Setting key')
        config_get_parser.set_defaults(func=config_get)
        config_set_parser = config_subparser.add_parser(
            'set', help='Set a configuration value'
        )
        config_set_parser.add_argument('key', help='Setting key')
        config_set_parser.add_argument('value', help='Value to set for the key')
        config_set_parser.set_defaults(func=config_set)

        args = parser.parse_args()
        config = RootConfig.load(config_path=args.config_path)
        config.save()
        return await args.func(config, args)
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        print(e)
    return 1


def run() -> int:
    return asyncio.run(main())

if __name__ == '__main__':
    sys.exit(run())
