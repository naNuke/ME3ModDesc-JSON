# ME3ModDesc-JSON

Written for Python 3.14.0

A python script to convert ME3Tweaks ModManager's moddesc.ini format into .json and back.  
It is meant to be used as CLI tool.

It does not fully cover all features of the moddesc format, only those that I needed so far.  
Dont comment on spaghetti code, I am not a Python pro, if you wanna fix things feel free to open a PR.

## Usage:

pip install -e .

## Commands:
convert  Convert data from a file to another format.
        _right now it just creates .ini from .json or vice-versa and replaces existing file if present_
debug    Prints repr() of the parsed values from the selected file.
echo     Prints conversion output into the console window.
test     Run a test against a file of supported format.
