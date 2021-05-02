PkgLic
======

The purpose of this helper app is to make it fast and easy to get an understanding of which open source licenses you are using in your application.

This is done by querying the respective package manager for the meta data rather than what is installed locally, as your build machines may not want/need all the packages installed and you'll also save time by not having to download the bulk of the packages.

The intention is to run it in your build pipeline, and at least you get a list of the components you use (as per your requirements files) and their licenses. With a small bit of trickery you can update some wiki page to keep an up to date list of packages.

The script handles `requirements.txt` (python), `package.json` (javascript), `*.csproj` (C#/nuget) and `packages.config` (C#/nuget).

Basic operation is to invoke the script with some file

    pkglic -f requirements.txt

and get an output with package names and their license. There are some more switches supported.

```
pkglic - (c) Jesper Hogstrom 2021.

usage: pkglic [-h] -f file [file ...] [-o {0,1,2,3}] [-t {py,js,cs,nu}]
              [-u [UNWANTED [UNWANTED ...]]] [-v]

optional arguments:
  -h, --help            show this help message and exit
  -f file [file ...], --files file [file ...]
                        input files to scan.
  -o {0,1,2,3}, --order {0,1,2,3}
                        Which fields to use to sort output; 0 - type, name, 1:
                        license, name, 2: type, license, 3: group by license.
  -t {cs,py,js,cs}, --type {cs,py,js,cs}
                        Assume <type> for all <files> if not guessable.
  -u [UNWANTED [UNWANTED ...]], --unwanted [UNWANTED [UNWANTED ...]]
                        Exit with errorlevel on these license types.
  -v, --verbose         Increase verbosity.
  --json file           Output as json-string to <file>.
  -w file, --whitelist file
                        Read whitelisted packages form <file>.
  ```

Installation
============

Either install it from pypi
```
pip install pkglic
```

or download the sources and make sure your environment has the required packages installed (`pip install -r requirements.txt`) and invoke the script from wherever you store it. You may for instance want to check it in with the rest of your build tools.

Supported formats
=================

Python
------
Any file path containing "requirements.txt" will be analyzed as a requirements file as supported by [pip](https://pip.pypa.io/en/stable/cli/pip_install/).

http://pypi.org is used to fetch the meta data.

Javascript
----------
Any file path containing "package.json" will be analyzed as a package.json file. Only the "dependencies" block will be checked!

https://npmjs.org is used to fetch the meta data.

Nuget
-----
Any file containing ".csproj" will be analyzed as a C# project and the nuget packages extracted for analysis.

Any file containing "packages.config" will be analyzed as a nuget packages file (an older way to specify nuget dependencies).


https://nuget.org is used to fetch the meta data.

Whitelisting
============
In some cases you'll find a package that lists as NOT_SPECIFIED or 404_NOT_FOUND, but you know from some other source the license it is actually used as. In that case you can add a `--whitelist` file. The whitelist file can be written as a text file or as a json formatted structure.

The textfile has the following format:
```
<package_name>[: <expected_license>[ -> <map_to_license>]]
```

Valid examples are
```
foo
foobar: NOT_SPECIFIED
barbaz: 404_NOT_FOUND -> MIT
```

The above file will
* whitelist `foo` no matter what license it presents.
* whitelist `foobar` if it presents as NOT_SPECIFIED.
* whitelist `barbaz` if it presents as 404_NOT_FOUND and remap it to MIT.

The remapping is used when listing the output as well as in the `--json` output.

If written as json, the following is eqiuvalent:
```
{
    "foo": {},
    "foobar": {"expected": "NOT_SPECIFIED"},
    "barbaz": {"expected": ""404_NOT_FOUND", "mapto": "MIT"}
}
```

Hard check on licenses
======================
Some projects prefer to avoid certain OSS licenses. This was actually the main reason for writing the tool. There are many ways to accomplish such a verification, including using the switch `-u` or `--unwanted` - for instance `-u GPL` or `-unwanted "MIT License"`.

Adding the `-u` switch will first print all packages and their licenses, then print out all packages that match any unwanted license and finally *terminate with an error code, breaking the build*.


Updating wiki pages
===================
All output is written to `stdout`, so something like
```
pkglic -f requirements.txt | tee /tmp/licenses_in_use
wikiupdater --host wiki.intranet --target-page licenses --upload /tmp/licenses_in_use
```
will do the trick (assuming you have a tool called wikiupdater etc etc).

Scanning all files in a tree
============================
If you have scattered your requirements.txt throughout your source tree, and even separated the development packages into dev-requirements.txt, you can use the existing tools to `find` all files and then add them to the command lline using `xargs`.

```
find -iname '*requirements.txt' | xargs pkglic -f
```

This can of course be combined with tee.
```
find -iname '*requirements.txt' | xargs python pkglic -f | tee /tmp/licenses_in_use
```

Note that adding multiple files may result in duplication of packages, if they appear in multiple files. This may add some value, as you will see in how many places (but not which places) you require each package. If that is more information than you need, then remove the duplicate lines with `uniq`.

```
find -iname '*requirements.txt' | xargs pkglic -f | uniq
```

Modifying the output
====================
If you are reasonably satisfied with the standard output format, but want to tweak it just a little, you can for instance remove the type indicator easily using `sed`.
```
find -iname '*requirements.txt' | xargs pkglic -f | | sed -e "s/\[.*\] //"
```

For heavy duty modification, add `--json <file>` to the argument list. That will yield standard output on stdout and a list of dictionaries in `<file>`. This should be easy enough to import into some other tool to generate a nifty report or a credits page.
```
pkglic -f requirements.txt --json licenses.json
generate_credits -i licenses.lic > credits-html
```
(assuming you have a tool `generate_credits` etc etc)

The output file has the following format:
```
{
    "generator": "pkglic (c) Jesper Hogstrom 2021",
    "generated": "date of execution in iso-format",
    "packages":
    [
        {
            "name": <packageid>,
            "version": <version>,
            "license": <license type or filename with license (prefixed by 'file: ')>,
            "licenseurl": <url with license text or null>,
            "author": <author or null>,
            "author_email": <author's email or null>,
            "home_page": <url to project's home page or null>,
            "summary": <summary or null>
        },
        ...
    ]
}
```

License types
=============

The license type will be set to whatever the package specifies. However, in some cases the license cannot be determined. If so, the following applies:

* The meta data could not be downloaded: 404_NOT_FOUND
* The license node not present: NOT_SPECIFIED
