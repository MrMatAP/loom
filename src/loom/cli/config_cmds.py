import argparse
import enum
import typing

import pydantic
import rich.console
import yaml

from loom.config import ConfigFile

console = rich.console.Console()


async def config_list(config: ConfigFile, args: argparse.Namespace) -> int:
    del args
    console.print(yaml.dump(config.model_dump(mode='json')))
    return 0


async def config_get(config: ConfigFile, args: argparse.Namespace) -> int:
    """
    Print the value of a configuration value in the configuration hierarchy.
    Args:
        config (ConfigFile): The configuration object
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


async def config_set(config: ConfigFile, args: argparse.Namespace) -> int:
    """
    Set a configuration value in the configuration hierarchy.
    Args:
        config (ConfigFile): The configuration object
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
    elif _is_secret_str_field(type(parent), leaf):
        setattr(parent, leaf, pydantic.SecretStr(args.value))
    else:
        setattr(parent, leaf, args.value)
    config.save()
    return 0


def _is_secret_str_field(model: type[pydantic.BaseModel], field_name: str) -> bool:
    """Whether the field is typed `SecretStr` (optionally `SecretStr | None`)."""
    annotation = model.model_fields[field_name].annotation
    if annotation is pydantic.SecretStr:
        return True
    return pydantic.SecretStr in typing.get_args(annotation)


def add_config_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register the `config` command group."""
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
