# ctlbase
An asyncio-based package for creating CLI utilities and service daemons.

Main features:

* Auto-discovery of related files for a certain Python module, e.g. a configuration file `etc/curtain` for a Python module `curtainctl.py`.
* Support for Bash-style configuration files, including variable interpolation, indexed and associative arrays.
* A common set of command line arguments for a family of related CLI utilities.
* Common output handling in plain text and JSON format, for *stdout* and/or log files.
* Exit code handling based on occurring error or warning messages.

For apidocs, see [https://xoocoon.github.io/ctlbase](https://xoocoon.github.io/ctlbase/html/).

# Core concepts

To understand the core concepts of the package, it is advisable to read the introduction of the [shell](https://xoocoon.github.io/ctlbase/html/shell.html) apidoc.

An example for a Bash-style configuration file is included in the repo under `templates/bashconfig`.

# Installation

## Package dependencies

The following packages need to be installed:

- [aiofiles](https://pypi.org/project/aiofiles/), for accessing files with asyncio
- [keyring](https://pypi.org/project/keyring/), if you want to retrieve credentials from your desktop environment's keyring, e.g. Seahorse

If you install *ctlbase* in a virtual Python environment, as recommended under [Package installation](#package-installation), dependecies will be installed automatically as needed.

For completeness, on a Debian-based system, the dependencies can be installed as follows:

```
sudo apt update && sudo apt install -y python3-aiofiles python3-keyring
```

## Package installation

For installing the *ctlbase* package you have at least to choices:

* Install the latest release from PyPI via `pip`.
* Install directly from a local clone of the GitHub repo.

In both cases, it is recommended to use a virtual Python environment to encapsulate all the required dependencies. See [https://docs.python.org/3/library/venv.html](https://docs.python.org/3/library/venv.html) for details on how to create one.

For installing from PyPI, use the `pip` command of your Python environment as follows:

```
pip install ctlbase
```

For installing from a local clone of the GitHub repo, use the `pip` command of your Python environment as follows:

```
pip install $CTLBASE_PATH
```

... where `$CTLBASE_PATH` resolves to the root directory of the repo containing the `pyproject.toml` file.

In both cases, if you want to retrieve credentials from your desktop environment's keyring, install the optional `keyring` dependency by appending `[keyring]` to the command.

> [!WARNING]
> On some Linux distributions, Python packages are managed by the system's package manager, especially Debian and derivatives. Installing *ctlbase* globally could break these system-wide packages. If you want to take the risk, install the system-wide packages listed under [Package dependencies](#package-dependencies). Then "dry-run" the *ctlbase* installation first, to see if these system-wide packages satisfy *ctlbase*'s dependencies. If only `Requirement already satisfied` messages occur, you may proceed with the actual installation.

For "dry-running" a global installation, consider the following command:

```
sudo pip install $CTLBASE_PATH --dry-run
```
