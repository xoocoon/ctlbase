# ctlbase
An asyncio-based framework for creating CLI utilities and service daemons.

For CLI utilities, it supports ...
  * ... sharing a common set of arguments across utlities, backed by default implementations.
  * ... message output in plain text or JSON to *stdout* and *stderr*.
  * ... setting so-called modes, i.e. persistent flags with an optional timeout after which the flag is unset automatically.

For both CLI utilities and service daemons, it supports ...
  * ... auto-discovery of associated files, e.g. configuration files.
  * ... parsing Bash-style configuration files.
  * ... reading credentials from a file, a keyring, TPM2 or a user prompt.
  * ... sophisticated message handling in English, supporting translations into other languages.
  * ... automated exit code generation based on error and warning messages.

For service daemons, it supports ...
  * ... logging to a file instead of or in addition to *stdout* and *stderr*.
  * ... rendering credentials in various output formats, to be picked up by systemd services.
  * ... automated logging of asyncio task results.
  
For apidocs, see [https://xoocoon.github.io/ctlbase](https://xoocoon.github.io/ctlbase/html/).

# Core concepts

To understand the core concepts of the package, it is advisable to read the introduction of the [control](https://xoocoon.github.io/ctlbase/html/control.html) apidoc.

An example for a Bash-style configuration file is included in the repo under `templates/etc/bashconfig`. It can be used to initialize a [Control](https://xoocoon.github.io/ctlbase/html/control.html#ctlbase.control.Control) instance or a [ControlShell](https://xoocoon.github.io/ctlbase/html/shell.html#ctlbase.shell.ControlShell) instance.

As an example for a i18next-like translation file, see the file `templates/locales/de/translation.json` in the repo. It can be used with the `addTranslations()` method of a [MessageBuffer](https://xoocoon.github.io/ctlbase/html/message.html#ctlbase.message.MessageBuffer) instance.

# Installation

## Package dependencies

The following packages need to be installed:

- [aiofiles](https://pypi.org/project/aiofiles/), for accessing files with asyncio
- [keyring](https://pypi.org/project/keyring/), if you want to retrieve credentials from your desktop environment's keyring, e.g. libsecret/Seahorse.

If you install *ctlbase* in a virtual Python environment, as recommended under [Package installation](#package-installation), dependencies will be installed automatically as needed.

On a Debian-based system, the dependencies can be installed as follows:

```
sudo apt update && sudo apt install -y python3-aiofiles python3-keyring
```

## Package installation

For installing the *ctlbase* package you have at least two choices:

* Install the latest release from PyPI via `pip`.
* Install directly from a local clone of the GitHub repo.

In both cases, it is recommended to use a virtual Python environment to encapsulate all the required dependencies. See [https://docs.python.org/3/library/venv.html](https://docs.python.org/3/library/venv.html) for details on how to create one.

For installing from PyPI, use the `pip` command of your target environment as follows:

```
pip install ctlbase
```

For installing from a local clone of the GitHub repo, use the `pip` command of your target environment as follows:

```
pip install $CTLBASE_PATH
```

... where `$CTLBASE_PATH` resolves to the root directory of the repo containing the `pyproject.toml` file.

In both cases, if you want to retrieve credentials from your desktop environment's keyring, install the optional `keyring` dependency by appending `[keyring]` to the command.

> [!WARNING]
> On some Linux distributions, Python packages are managed by the system's package manager, especially Debian and derivatives. Installing *ctlbase* globally could break these system-wide packages. On these systems, a virtual Python environment is strongly recommended.
