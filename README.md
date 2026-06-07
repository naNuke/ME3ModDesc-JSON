# ME3ModManager-PyTools

Written for Python 3.14.0

A python script to convert ME3Tweaks ModManager's moddesc.ini format into .json and back.  
Other little functions to do stuff with, like print same files within two mod folders.  
It is meant to be used as CLI tool.

It does not fully cover all features of the moddesc format, only those that I needed so far.  
Dont comment on spaghetti code, I am not a Python pro, if you wanna fix things feel free to open a PR.

## Usage:

install globally with: `pip install -e .`
uninstall: `pip uninstall me3mm`

module name: me3mm

| Command   | Description                                                                                                                              |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| conflicts | Print conflicting files between two mods                                                                                                 |
| convert   | Convert data from a file to another format.right now it just creates .ini from .json or vice-versa and replaces existing file if present |
| debug     | Prints repr() of the parsed values from the selected file.                                                                               |
| echo      | Prints conversion output into the console window.                                                                                        |
| test      | Run a test against a file of supported format.                                                                                           |
