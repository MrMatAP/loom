import argparse
import asyncio
import pathlib
from collections.abc import Callable

from loom.config import ConfigFile


def run_cli[C: ConfigFile](
    prog: str,
    default_path: pathlib.Path,
    config_cls: type[C],
    populate: Callable[[argparse._SubParsersAction], None],
    path_hint: str,
) -> int:
    """Shared entry-point plumbing for `loom` and `loom-admin`: parse
    arguments, load that CLI's own configuration model, and dispatch."""

    async def _main() -> int:
        try:
            parser = argparse.ArgumentParser(prog)
            parser.add_argument(
                '--config',
                type=pathlib.Path,
                required=False,
                dest='config_path',
                default=default_path,
                help=f'Path to the config file, defaults to {default_path} '
                f'(or ${path_hint} if set)',
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
            populate(parser.add_subparsers(required=True, help='Sub-commands'))
            args = parser.parse_args()
            config = config_cls.load(config_path=args.config_path)
            config.save()
            return await args.func(config, args)
        except KeyboardInterrupt:
            return 0
        except Exception as e:  # noqa: BLE001
            print(e)
        return 1

    return asyncio.run(_main())
