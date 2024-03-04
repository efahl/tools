#!/usr/bin/env python3
# vim: set expandtab softtabstop=4 shiftwidth=4:
# Copyright (C) 2022-2024 Eric Fahlgren


import subprocess as _SP


def get_status_output(cmd, *args, limit=None, **kwds):
    """ Back in Olden Times, Python 2 had a stdlib function called
        'getstatusoutput'.  It had several design flaws, which caused it to
        be kicked out of the stdlib, but it was nonetheless pretty useful for
        hacking together little command line tools.  I've reproduced it here,
        adding a couple of things and leaving others out.

        My version always invokes a system shell (see your $SHELL variable
        for exactly what), and handles all output from the command as UTF-8.
        It also closes stdin on the subprocess, so that your code fails on a
        closed handle instead of hanging silently if the subprocess attempts
        to read from stdin.

        I've added a 'limit' parameter, which sets the time to wait (seconds)
        for the subprocess to respond.  When the timeout fails, an error
        message is returned in 'output' detailing the timeout.
    """
    kwds.setdefault('encoding', 'UTF-8')      # We're a modern app, so do modern app things.
    kwds.setdefault('errors',   'surrogateescape')

    kwds.setdefault('shell',    True)
    kwds.setdefault('stdin',    _SP.DEVNULL)  # Null device so subprocess doesn't hang when requesting input.
    kwds.setdefault('stdout',   _SP.PIPE)
    kwds.setdefault('stderr',   _SP.STDOUT)   # We re-route stderr -> stdout for simplicity.

    with _SP.Popen(cmd, *args, **kwds) as process:
        try:
            stdout_txt, stderr_text = process.communicate(timeout=limit)
        except _SP.TimeoutExpired as exc:
            stdout_txt = str(exc)

        if process.stdout and not process.stdout.closed:
            # Make sure to grab the tail end of the buffer after the
            # process has terminated.
            stdout_txt += process.stdout.read()
            process.stdout.close()

        stdout_txt = stdout_txt.replace('\r', '').rstrip()
        status = process.wait()
        if process.stdin:
            process.stdin.close()
    return status, stdout_txt
