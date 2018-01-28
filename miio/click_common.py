"""Click commons.

This file contains common functions for cli tools.
"""
import sys
if sys.version_info < (3, 4):
    print("To use this script you need python 3.4 or newer, got %s" %
          sys.version_info)
    sys.exit(1)
import click
import ipaddress
import miio
import logging
from typing import Union
from functools import wraps
from functools import partial


_LOGGER = logging.getLogger(__name__)


def validate_ip(ctx, param, value):
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError as ex:
        raise click.BadParameter("Invalid IP: %s" % ex)


def validate_token(ctx, param, value):
    token_len = len(value)
    if token_len != 32:
        raise click.BadParameter("Token length != 32 chars: %s" % token_len)
    return value


class ExceptionHandlerGroup(click.Group):
    """Add a simple group for catching the miio-related exceptions.

    This simplifies catching the exceptions from different click commands.

    Idea from https://stackoverflow.com/a/44347763
    """
    def __call__(self, *args, **kwargs):
        try:
            return self.main(*args, **kwargs)
        except miio.DeviceException as ex:
            _LOGGER.debug("Exception: %s", ex, exc_info=True)
            click.echo(click.style("Error: %s" % ex, fg='red', bold=True))


class GlobalContextObject:
    def __init__(self, debug: int=0):
        self.debug = debug


class DeviceGroupMeta(type):

    device_classes = set()

    def __new__(mcs, name, bases, namespace) -> type:
        commands = {}
        for key, val in namespace.items():
            if not callable(val):
                continue
            device_group_command = getattr(val, '_device_group_command', None)
            if device_group_command is None:
                continue
            commands[device_group_command.command_name] = device_group_command

        namespace['_device_group_commands'] = commands
        if 'get_device_group' not in namespace:

            def get_device_group(dcls):
                return DeviceGroup(dcls)

            namespace['get_device_group'] = classmethod(get_device_group)

        cls = super().__new__(mcs, name, bases, namespace)
        mcs.device_classes.add(cls)
        return cls


class DeviceGroup(click.MultiCommand):

    class Command:
        def __init__(self, name, decorators, **kwargs):
            self.name = name
            self.decorators = list(decorators)
            self.decorators.reverse()
            self.kwargs = kwargs

        def __call__(self, func):
            self.func = func
            func._device_group_command = self
            self.kwargs.setdefault('help', self.func.__doc__)
            return func

        @property
        def command_name(self):
            return self.name or self.func.__name__.lower()

        def wrap(self, func):
            for decorator in self.decorators:
                func = decorator(func)
            return click.command(self.command_name, **self.kwargs)(func)

        def call(self, owner, *args, **kwargs):
            method = getattr(owner, self.func.__name__)
            return method(*args, **kwargs)

    DEFAULT_PARAMS = [
        click.Option(['--ip'], required=True, callback=validate_ip),
        click.Option(['--token'], required=True, callback=validate_token),
    ]

    def __init__(self, device_class, name=None, invoke_without_command=False,
                 no_args_is_help=None, subcommand_metavar=None, chain=False,
                 result_callback=None, result_callback_pass_device=True,
                 **attrs):

        self.commands = getattr(device_class, '_device_group_commands', None)
        if self.commands is None:
            raise RuntimeError(
                "Class {} doesn't use DeviceGroupMeta meta class."
                " It can't be used with DeviceGroup."
            )

        self.device_class = device_class
        self.device_pass = click.make_pass_decorator(device_class)

        attrs.setdefault('params', self.DEFAULT_PARAMS)
        attrs.setdefault('callback', click.pass_context(self.group_callback))
        if result_callback_pass_device and callable(result_callback):
            result_callback = self.device_pass(result_callback)

        super().__init__(name or device_class.__name__.lower(),
                         invoke_without_command, no_args_is_help,
                         subcommand_metavar, chain, result_callback, **attrs)

    def group_callback(self, ctx, *args, **kwargs):
        gco = ctx.find_object(GlobalContextObject)
        if gco:
            kwargs['debug'] = gco.debug
        ctx.obj = self.device_class(*args, **kwargs)

    def command_callback(self, command, device, *args, **kwargs):
        return command.call(device, *args, **kwargs)

    def get_command(self, ctx, cmd_name):
        cmd = self.commands[cmd_name]
        return self.commands[cmd_name].wrap(self.device_pass(partial(
            self.command_callback, cmd
        )))

    def list_commands(self, ctx):
        return sorted(self.commands.keys())


def device_command(*decorators, name=None, **kwargs):
    return DeviceGroup.Command(name, decorators, **kwargs)


def echo_return_status(msg_fmt: Union[str, callable]="",
                       result_msg_fmt: Union[str, callable]="{result}"):
    def decorator(func):
        @wraps(func)
        def wrap(*args, **kwargs):
            if msg_fmt:
                if callable(msg_fmt):
                    msg = msg_fmt(**kwargs)
                else:
                    msg = msg_fmt.format(**kwargs)
                if msg:
                    click.echo(msg.strip())
            kwargs['result'] = func(*args, **kwargs)
            if result_msg_fmt:
                if callable(result_msg_fmt):
                    result_msg = result_msg_fmt(**kwargs)
                else:
                    result_msg = result_msg_fmt.format(**kwargs)
                if result_msg:
                    click.echo(result_msg.strip())
        return wrap
    return decorator