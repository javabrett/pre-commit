# -*- coding: utf-8 -*-
from __future__ import absolute_import
from __future__ import unicode_literals

import concurrent.futures
import sys
import time

import mock
import pytest
import six

from pre_commit import parse_shebang
from pre_commit import xargs


@pytest.mark.parametrize(
    ('env', 'expected'),
    (
        ({}, 0),
        ({b'x': b'1'}, 12),
        ({b'x': b'12'}, 13),
        ({b'x': b'1', b'y': b'2'}, 24),
    ),
)
def test_environ_size(env, expected):
    # normalize integer sizing
    assert xargs._environ_size(_env=env) == expected


@pytest.fixture
def win32_py2_mock():
    with mock.patch.object(sys, 'getfilesystemencoding', return_value='utf-8'):
        with mock.patch.object(sys, 'platform', 'win32'):
            with mock.patch.object(six, 'PY2', True):
                yield


@pytest.fixture
def win32_py3_mock():
    with mock.patch.object(sys, 'getfilesystemencoding', return_value='utf-8'):
        with mock.patch.object(sys, 'platform', 'win32'):
            with mock.patch.object(six, 'PY2', False):
                yield


@pytest.fixture
def linux_mock():
    with mock.patch.object(sys, 'getfilesystemencoding', return_value='utf-8'):
        with mock.patch.object(sys, 'platform', 'linux'):
            yield


def test_partition_trivial():
    assert xargs.partition(('cmd',), (), 1) == (('cmd',),)


def test_partition_simple():
    assert xargs.partition(('cmd',), ('foo',), 1) == (('cmd', 'foo'),)


def test_partition_limits():
    ret = xargs.partition(
        ('ninechars',), (
            # Just match the end (with spaces)
            '.' * 5, '.' * 4,
            # Just match the end (single arg)
            '.' * 10,
            # Goes over the end
            '.' * 5,
            '.' * 6,
        ),
        1,
        _max_length=21,
    )
    assert ret == (
        ('ninechars', '.' * 5, '.' * 4),
        ('ninechars', '.' * 10),
        ('ninechars', '.' * 5),
        ('ninechars', '.' * 6),
    )


def test_partition_limit_win32_py3(win32_py3_mock):
    cmd = ('ninechars',)
    # counted as half because of utf-16 encode
    varargs = ('😑' * 5,)
    ret = xargs.partition(cmd, varargs, 1, _max_length=21)
    assert ret == (cmd + varargs,)


def test_partition_limit_win32_py2(win32_py2_mock):
    cmd = ('ninechars',)
    varargs = ('😑' * 5,)  # 4 bytes * 5
    ret = xargs.partition(cmd, varargs, 1, _max_length=31)
    assert ret == (cmd + varargs,)


def test_partition_limit_linux(linux_mock):
    cmd = ('ninechars',)
    varargs = ('😑' * 5,)
    ret = xargs.partition(cmd, varargs, 1, _max_length=31)
    assert ret == (cmd + varargs,)


def test_argument_too_long_with_large_unicode(linux_mock):
    cmd = ('ninechars',)
    varargs = ('😑' * 10,)  # 4 bytes * 10
    with pytest.raises(xargs.ArgumentTooLongError):
        xargs.partition(cmd, varargs, 1, _max_length=20)


def test_partition_target_concurrency():
    ret = xargs.partition(
        ('foo',), ('A',) * 22,
        4,
        _max_length=50,
    )
    assert ret == (
        ('foo',) + ('A',) * 6,
        ('foo',) + ('A',) * 6,
        ('foo',) + ('A',) * 6,
        ('foo',) + ('A',) * 4,
    )


def test_partition_target_concurrency_wont_make_tiny_partitions():
    ret = xargs.partition(
        ('foo',), ('A',) * 10,
        4,
        _max_length=50,
    )
    assert ret == (
        ('foo',) + ('A',) * 4,
        ('foo',) + ('A',) * 4,
        ('foo',) + ('A',) * 2,
    )


def test_argument_too_long():
    with pytest.raises(xargs.ArgumentTooLongError):
        xargs.partition(('a' * 5,), ('a' * 5,), 1, _max_length=10)


def test_xargs_smoke():
    ret, out, err = xargs.xargs(('echo',), ('hello', 'world'))
    assert ret == 0
    assert out == b'hello world\n'
    assert err == b''


exit_cmd = parse_shebang.normalize_cmd(('bash', '-c', 'exit $1', '--'))
# Abuse max_length to control the exit code
max_length = len(' '.join(exit_cmd)) + 3


def test_xargs_negate():
    ret, _, _ = xargs.xargs(
        exit_cmd, ('1',), negate=True, _max_length=max_length,
    )
    assert ret == 0

    ret, _, _ = xargs.xargs(
        exit_cmd, ('1', '0'), negate=True, _max_length=max_length,
    )
    assert ret == 1


def test_xargs_negate_command_not_found():
    ret, _, _ = xargs.xargs(('cmd-not-found',), ('1',), negate=True)
    assert ret != 0


def test_xargs_retcode_normal():
    ret, _, _ = xargs.xargs(exit_cmd, ('0',), _max_length=max_length)
    assert ret == 0

    ret, _, _ = xargs.xargs(exit_cmd, ('0', '1'), _max_length=max_length)
    assert ret == 1


def test_xargs_concurrency():
    bash_cmd = parse_shebang.normalize_cmd(('bash', '-c'))
    print_pid = ('sleep 0.5 && echo $$',)

    start = time.time()
    ret, stdout, _ = xargs.xargs(
        bash_cmd, print_pid * 5,
        target_concurrency=5,
        _max_length=len(' '.join(bash_cmd + print_pid)) + 1,
    )
    elapsed = time.time() - start
    assert ret == 0
    pids = stdout.splitlines()
    assert len(pids) == 5
    # It would take 0.5*5=2.5 seconds ot run all of these in serial, so if it
    # takes less, they must have run concurrently.
    assert elapsed < 2.5


def test_thread_mapper_concurrency_uses_threadpoolexecutor_map():
    with xargs._thread_mapper(10) as thread_map:
        assert isinstance(
            thread_map.__self__, concurrent.futures.ThreadPoolExecutor,
        ) is True


def test_thread_mapper_concurrency_uses_regular_map():
    with xargs._thread_mapper(1) as thread_map:
        assert thread_map is map
