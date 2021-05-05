#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import requests
import json
import argparse
import logging
import datetime
import re
from typing import List, Type
import concurrent.futures
from lxml import etree
from collections import defaultdict
from jinja2 import Template
# from pprint import pprint
try:
    from .authinfo import PROGRAM_NAME, AUTHOR, VERSION
except ImportError:
    from authinfo import PROGRAM_NAME, AUTHOR, VERSION

SORTORDER = {
    0: lambda x: [x.type(), x.name],
    1: lambda x: [x.license, x.name],
    2: lambda x: [x.type(), x.license],
    3: "g"
}

logger = logging.getLogger(PROGRAM_NAME)
logger.setLevel(logging.DEBUG)


class PackageInfo:
    def __init__(self, name: str, version: str, filename: str):
        self.name = name
        self.version = version
        self.filename = filename
        self.license = "NOT_DOWNLOADED"
        self.licenseurl = ""
        self.author = None
        self.author_email = None
        self.home_page = None
        self.summary = None
        self.whitelisted = False
        self.remapped = False

    def __str__(self):
        name = self.name
        if self.version is not None:
            name += f" {self.version}"
        props = []
        if self.licenseurl:
            props.append(self.licenseurl)
        if self.remapped:
            props.append(f"(remapped from '{self.orglicense}')")
        if self.whitelisted:
            props.append("(whitelisted)")
        return f"[{self.type()}] {name:30} {self.license} {' '.join(props)}".strip()

    def clean_license(self, license: str) -> str:
        MAXLEN = 50
        license = license.replace('"', '')
        if license == "":
            return "NOT_SPECIFIED"
        if license.startswith("GNU LESSER GENERAL PUBLIC LICENSE"):
            return "LGPL"
        if len(license) > MAXLEN:
            return license[:MAXLEN] + "..."
        return license

    def asjson(self):
        return {
            "name": self.name,
            "version": self.version,
            "license": self.license,
            "licenseurl": self.licenseurl,

            "author": self.author,
            "author_email": self.author_email,
            "home_page": self.home_page,
            "summary": self.summary
        }


class NpmPackageInfo(PackageInfo):
    @property
    def url(self) -> str:
        return f"https://registry.npmjs.org/{self.name}/{self.version}"

    @classmethod
    def type(self) -> str:
        return "js"

    def update_metadata(self, s: str) -> None:
        d = json.loads(s)

        self.license = self.clean_license(d.get("license", "NOT_SPECIFIED"))

        author = d.get("author")
        if author is not None:
            self.author = author.get("name")
            self.author_email = author.get("email")
        self.home_page = d.get("home_page")
        self.summary = d.get("description")

    @classmethod
    def can_parse(self, filename: str) -> bool:
        return "package.json" in filename

    @classmethod
    def parse(self, filename: str) -> List[PackageInfo]:
        """
        Create information for all packages found in filename.
        Filename is expected to be in the package.json format.

        Args:
            filename (str): Name of file to read.

        Returns:
            list: List of PackageInfo instances.
        """
        def clean_version(v: str) -> str:
            # https://docs.npmjs.com/about-semantic-versioning
            return v.replace(".x", ".0").replace("~", "").replace("^", "")

        dependencies = json.load(open(filename)).get("dependencies", {})
        return [NpmPackageInfo(name, clean_version(version), filename) for name, version in dependencies.items()]


class PythonPackageInfo(PackageInfo):
    @property
    def url(self) -> str:
        if self.version is not None:
            return f"https://pypi.org/pypi/{self.name}/{self.version}/json"
        return f"https://pypi.org/pypi/{self.name}/json"

    @classmethod
    def type(self) -> str:
        return "py"

    def update_metadata(self, s: str) -> None:
        d = json.loads(s)
        self.license = self.clean_license(d.get("info", {}).get("license", "NOT_SPECIFIED"))
        self.version = d.get("info", {}).get("version", "??")
        self.author = d.get("info", {}).get("author")
        self.author_email = d.get("info", {}).get("author_email")
        self.home_page = d.get("info", {}).get("home_page")
        self.summary = d.get("info", {}).get("summary")

    @classmethod
    def can_parse(self, filename: str) -> bool:
        return "requirements.txt" in filename

    @classmethod
    def parse(self, filename: str) -> List[PackageInfo]:
        """
        Create information for all packages found in filename.
        Filename is expected to be in the requirements.txt format.

        Args:
            filename (str): Name of file to read.

        Returns:
            list: List of PackageInfo instances.
        """
        files = [_.split("#")[0].strip() for _ in open(filename).readlines() if not _.startswith("#")]
        fetchinfo = []
        name_regexp = "(.*)[=>~]="
        version_regexp = "[=>~]=(.*)"
        re_name = re.compile(name_regexp)
        re_version = re.compile(version_regexp)

        for f in files:
            f = f.split(";")[0].split(",")[0].split("[")[0]
            name = re_name.search(f)
            version = re_version.search(f)

            if version is not None:
                version = version.group(1).strip()

            if name is None:
                name = f.strip()
            else:
                name = name.group(1).strip()

            fetchinfo.append(PythonPackageInfo(name, version, filename))
        return fetchinfo


class CSharpPackageInfo(PackageInfo):
    @property
    def url(self) -> str:
        # https://docs.microsoft.com/en-us/nuget/api/package-base-address-resource
        return f"https://api.nuget.org/v3-flatcontainer/{self.name}/{self.version}/{self.name}.nuspec".lower()

    @classmethod
    def type(self) -> str:
        return "cs"

    @classmethod
    def can_parse(self, filename: str) -> bool:
        return ".csproj" in filename

    @classmethod
    def parse(self, filename: str) -> List[PackageInfo]:
        """
        Create information for all packages found in filename.
        Filename is expected to be in the *.csproj format.

        Args:
            filename (str): Name of file to read.

        Returns:
            list: List of PackageInfo instances.
        """
        doc = etree.parse(filename)
        packagenode = doc.xpath("//PackageReference")
        return [CSharpPackageInfo(
                    n.xpath("./@Include")[0],
                    n.xpath("./@Version")[0],
                    filename) for n in packagenode]

    def update_metadata(self, s: str) -> None:
        # https://docs.microsoft.com/en-us/nuget/reference/nuspec
        # print(s)

        doc = etree.XML(s)
        #
        # There is a namespace defined, but not explicitly used.
        # This made all my xpath queries return None until I mapped the
        # None-namespace to a proper (but arbitrary) prefix, and used that prefix
        # when querying.
        # After downloading a bunch of packages, it turmed out that not exactly all
        # nuspec files contain the namespace definition, hence the conditional
        # insertion of the prefixed namespace (and use of prefix).
        #
        nsmap = {}
        namespace = ""
        if None in doc.nsmap:
            nsmap["nuget"] = doc.nsmap[None]
            namespace = "nuget:"

        # licenseUrl is deprecated, but there are still traces of it in many packages
        licenseurls = doc.xpath(f"//{namespace}licenseUrl/text()", namespaces=nsmap)
        if len(licenseurls) > 0:
            self.licenseurl = licenseurls[0]
        license = doc.xpath(f"//{namespace}license", namespaces=nsmap)
        if len(license) > 0:
            lictype = license[0].xpath("./@type")[0]
            licvalue = license[0].xpath("./text()")[0]
            if lictype == "expression":
                self.license = self.clean_license(licvalue)
            else:
                self.license = f"{lictype}: {licvalue}"
        else:
            self.license = "NOT_SPECIFIED"

        self.author = (doc.xpath(f"//{namespace}authors/text()", namespaces=nsmap) + [None])[0]
        self.home_page = (doc.xpath(f"//{namespace}projectUrl/text()", namespaces=nsmap) + [None])[0]
        self.summary = (doc.xpath(f"//{namespace}description/text()", namespaces=nsmap) + [None])[0]


class NugetPackageInfo(CSharpPackageInfo):
    @classmethod
    def can_parse(self, filename: str) -> bool:
        return "packages.config" in filename

    @classmethod
    def parse(self, filename: str) -> List[PackageInfo]:
        """
        Create information for all packages found in filename.
        Filename is expected to be in the packages.config format.

        Args:
            filename (str): Name of file to read.

        Returns:
            list: List of PackageInfo instances.
        """
        doc = etree.parse(filename)
        packagenode = doc.xpath("//package")
        return [CSharpPackageInfo(
                    n.xpath("./@id")[0],
                    n.xpath("./@version")[0],
                    filename) for n in packagenode]


#
# Register parser classes here to make them participate in the fun.
#
PARSERS: List[Type] = [
    CSharpPackageInfo,
    PythonPackageInfo,
    NpmPackageInfo,
    NugetPackageInfo]


def update_package_info(package_info: PackageInfo, verbose: bool) -> None:
    """
    Fetch metadata for package_info and update the license property.

    Args:
        package_info (PackageInfo): Instance containing package information.
        verbose (bool): Increase verbosity if True.
    """
    if verbose:
        print(f"Downloading {package_info.name} from {package_info.filename}.")
    r = requests.get(package_info.url)
    if r.status_code == 404:
        logger.warning(f"{package_info.name} - not found at {package_info.url}")
        package_info.license = "404_NOT_FOUND"
        return

    package_info.update_metadata(r.content)


def fetch_package_infos(fetchinfo: List[PackageInfo], verbose: bool) -> None:
    """
    Initiate workers that fetch metadata for all packages.

    Args:
        fetchinfo (list): List of packages to fetch metadata for.
        verbose (bool): Increase verbosity if True.
    """
    if not verbose:
        print("Fetching package meta data...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(update_package_info,  info, verbose) for info in fetchinfo]
    [f.result() for f in futures]
    print()


def get_parser(filename: str, default_type: str) -> Type:
    """
    Get a parser for the file `filename`.

    Args:
        filename (str): Path to file.
        default_type (str): Default if not possible to determine type based on name.

    Returns:
        Returns a parser that can parse the file type.
    """
    for p in PARSERS:
        if p.can_parse(filename):
            return p

    for p in PARSERS:
        if p.type() == default_type:
            return p


def acquire_package_info(
        filenames: List[str],
        default_type: str,
        verbose: bool,
        exclude_packages: List[str],
        make_unique: bool) -> List[PackageInfo]:
    """
    Download the metadata for all packages.

    Args:
        default_type (str): Default type if not possible to determine from file names.
        verbose (bool): Increase verbosity if True.
        exclude_packages: (List[str]): List of packages to exclude from checking.

    Returns:
        list: List of PackageInfo instances.
    """
    packages = []
    for filename in filenames:
        parser = get_parser(filename, default_type)
        packages += parser.parse(filename)

    packages = [p for p in packages if p.name not in exclude_packages]

    if make_unique:
        unique = []
        for p in packages:
            if all([_.name != p.name for _ in unique]):
                unique.append(p)
        print(f"Eliminated {len(packages) - len(unique)} duplicates based on name.")
        packages = unique

    fetch_package_infos(packages, verbose)
    return packages


def print_package_info(packages: List[PackageInfo], sortkey) -> None:
    """
    Print the package info ordered by the lambda function sortkey.

    Args:
        packages (list): List of PackageInfo instances
        sortkey: Lambda-function used to sort the output.
    """
    if sortkey == "g":
        groups = defaultdict(list)
        for p in packages:
            groups[f"{p.license} {p.licenseurl}"].append(p)
        for key, plist in groups.items():
            print(f"{key}")
            for p in plist:
                print(f"\t* {p}")
            print()
    else:
        for package in sorted(packages, key=sortkey):
            print(package)

    print(f"\n{len(packages)} packages checked.")


def detect_unwanted_licenses(packages: List[PackageInfo], unwanted_licenses: List[str]) -> bool:
    """
    Detect if any of the licenses are 'unwanted' and if so print them.

    Args:
        packages (List[PackageInfo]): List of PackageInfo instances.
        unwanted_licenses (List[str]): List of licenses that are unwanted.

    Returns:
        bool: Returns true if there are unwanted licenses present
    """
    unwanted_upper = [_.upper() for _ in unwanted_licenses]
    unwanted = [p for p in packages if p.license in unwanted_upper and not p.whitelisted]
    if unwanted:
        print("\nUnwanted license(s) detected!")
        for p in sorted(unwanted, key=SORTORDER[0]):
            print(f"[{p.type()}] '{p.name}' from {p.filename} uses unwanted license '{p.license}'.")
        return True
    return False


def init_argparse() -> argparse.ArgumentParser:
    """
    Initiate argument parser.
    """
    parser = argparse.ArgumentParser(prog=PROGRAM_NAME, fromfile_prefix_chars='@')
    parser.add_argument(
        '-f', "--files",
        metavar='file',
        action='append',
        required=True,
        help='input files to scan.')
    parser.add_argument(
        "-t", "--type",
        choices=[p.type() for p in PARSERS],
        default=None,
        help="Assume <type> for all --files if not guessable by filename.")
    parser.add_argument(
        "--uniq",
        action="store_true",
        help="Remove duplicate packages.")
    parser.add_argument(
        "-x", "--exclude",
        metavar="file|package",
        action='append',
        default=[],
        help="Do not check (or list) excluded packages.")
    parser.add_argument(
        "-u", "--unwanted",
        metavar="package",
        action='append',
        default=[],
        help="Exit with errorlevel on these license types.")
    parser.add_argument(
        "-w", "--whitelist",
        metavar="file|package",
        action='append',
        default=[],
        help="Read whitelisted packages form <file>.")
    parser.add_argument(
        "-o", "--order",
        type=int,
        choices=SORTORDER.keys(),
        default=list(SORTORDER.keys())[0],
        help="Which fields to use to sort output; 0 - type, name, 1: license, name, 2: type, license, 3: group by license.")  # noqae501
    parser.add_argument(
        "--json",
        metavar="file",
        default=None,
        help="Output as json-string to <file>.")
    parser.add_argument(
        "--credits",
        metavar="file",
        default=None,
        help="Generate a credits file.")
    parser.add_argument(
        "--creditstemplate",
        metavar="file",
        default=None,
        help="Template used to generate credits file.")
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Increase verbosity.")


    # Automatically add the parameter file args.txt if it exists.
    if os.path.exists("args.txt") and "@args.txt" not in sys.argv:
        sys.argv.append("@args.txt")

    return parser


def print_packages_to_json(packages: List[PackageInfo], filename: str) -> None:
    """
    Generate a json-string with all packages and write to 'filename'.

    Args:
        packages (List[PackageInfo]): List of PackageInfo
        filename (str): File to write to.
    """
    if filename is None:
        return
    res = {
        "generator": f"{PROGRAM_NAME} {VERSION} (c) {AUTHOR} 2021",
        "generated": datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        "packages": [p.asjson() for p in packages]
    }

    with open(filename, "w") as f:
        f.write(json.dumps(res))
    print(f"\nJson-data written to '{filename}'.")


def get_whitelisted(whitelisted: List[str]) -> dict:
    """
    Examine the content of whitelisted and return a json structure with whitelisting.

    Args:
        whitelisted (List[str]): List of strings with either files or packages.

    Returns:
        dict: Dictionary with whitelist info.
    """
    def update_res(package: str) -> str:
        if package.strip().startswith("#"):
            return
        parts = package.split(":")
        if len(parts) == 1:
            parts.append("*")
        licmap = parts[1].split("->")
        if len(licmap) == 1:
            licmap.append("")
        res[parts[0].strip()] = {"expect": licmap[0].strip(), "mapto": licmap[1].strip()}

    if not whitelisted:
        logger.debug("No whitelisted packages.")
        return None
    res = {}
    for w in whitelisted:
        if not os.path.exists(w):
            update_res(w)
        else:
            with open(w) as f:
                try:
                    res = res + json.load(f)
                except json.decoder.JSONDecodeError:
                    res = {}
                    logger.debug(f"{w} not in json format. Attempting line-by-line.")
                    f.seek(0)
                    for line in f.readlines():
                        if line.strip().startswith("#"):
                            continue
                        parts = line.split(":")
                        if len(parts) == 1:
                            parts.append("*")
                        licmap = parts[1].split("->")
                        if len(licmap) == 1:
                            licmap.append("")
                        res[parts[0].strip()] = {"expect": licmap[0].strip(), "mapto": licmap[1].strip()}
                    return res
                except Exception as e:
                    logger.exception(e)

    return res


def apply_whitelisting(packages: List[PackageInfo], whitelisting: dict) -> List[PackageInfo]:
    """
    Apply the whitelisting information and update `packages`.

    Args:
        packages (List[PackageInfo]): List of PackageInfo instances.
        whitelisting (dict): Whitelisting information.

    Returns:
        List[PackageInfo]: Updated list of PackageInfo with whitelisting information applied.
    """
    if whitelisting is None:
        return packages
    res = []
    for p in packages:
        if p.name not in whitelisting:
            res.append(p)
            continue
        whitelist = whitelisting[p.name]
        if whitelist.get("expect", "*") == "*" and whitelist.get("mapto", "") == "":
            p.whitelisted = True
        elif whitelist.get("expect", "*") == "*":
            p.whitelisted = True
            p.orglicense = p.license
            p.license = whitelist.get("mapto", "")
            p.remapped = True
        elif p.license == whitelist.get("expect", "*") and whitelist.get("mapto", "") == "":
            p.whitelisted = True
        elif p.license == whitelist.get("expect", "*"):
            p.whitelisted = True
            p.orglicense = p.license
            p.license = whitelist.get("mapto", "")
            p.remapped = True
        res.append(p)
    return res


def get_excluded(exclude_list: List[str]) -> List[str]:
    """
    Retrieve excluded packages from `exclude_list`.

    Args:
        exclude_list (List[str]): Filenames containing packages to exclude or individual packages.

    Returns:
        List[str]: List of packages not to check.
    """
    res = []

    for exclude in exclude_list:
        if os.path.exists(exclude):
            with open(exclude) as f:
                res.extend([p.strip() for p in f.readlines() if not p.strip().startswith("#")])
        else:
            if "," in exclude:
                res.extend([p.strip() for p in exclude.split(",")])
            else:
                res.append(exclude)

    return res


def verify_file(filename: str, *, required: bool = True, argname: str = "") -> None:
    """
    Verify the filename is set and if required also existing. If validation fails the
    application with terminate with an error code.

    Args:
        filename (str): Name of file
        required (bool, optional): Indicates if the filename must be set. Defaults to True.
        argname (str, optional): Name of parameter - used in error message. Defaults to "".
    """
    if filename is None:
        if required:
            print(f"Missing parameter '{argname}'. Cannot continue.")
            exit(1)
        return

    if not os.path.exists(filename):
        print(f"The file '{filename}' could not be found. Terminating.")
        exit(1)


def create_credits_file(packages: List[PackageInfo], sortkey, outfile: str, templatefile: str) -> None:
    """
    Create a summary file based on `templatefile` and write it to `outfile`.
    Data will be sorted according to sortkey.

    Args:
        packages (List[PackageInfo]): List of packages.
        sortkey ([type]): Lambda to sort the packages.
        outfile (str): File into which to write the output.
        templatefile (str): Template used for expansion.
    """
    with open(templatefile) as f:
        template = Template(f.read())

    data = {
        "packages": sorted(packages, key=sortkey),
        "program": PROGRAM_NAME,
        "author": AUTHOR}

    with open(outfile, "w") as f:
        f.write(template.render(data))

    print(f"Credits written to '{outfile}'.")


def validate_args(args) -> None:
    """
    Validates command lline arguments.

    Args:
        args: The command line arguments namespace.
    """
    for f in args.files:
        verify_file(f)
    if args.credits is not None:
        if args.creditstemplate is None:
            here = os.path.abspath(os.path.dirname(__file__))
            args.creditstemplate = os.path.join(here, "creditstemplate.txt")
        verify_file(args.creditstemplate, argname="creditstemplate")
    if args.creditstemplate is not None and args.credits is None:
        print(f"Need --credits <file> if --creditstemplate is specified ('{args.creditstemplate}').")
        exit(1)


def main():
    """
    Main function of the script.
    """
    print(f"{PROGRAM_NAME} {VERSION} - (c) {AUTHOR} 2021.")
    print(f"Executed {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}.\n")
    parser = init_argparse()
    args = parser.parse_args()

    validate_args(args)

    exclude_packages = get_excluded(args.exclude)
    whitelisting = get_whitelisted(args.whitelist)

    packages = acquire_package_info(
        args.files,
        args.type,
        args.verbose,
        exclude_packages,
        args.uniq)
    packages = apply_whitelisting(packages, whitelisting)
    print_package_info(packages, SORTORDER[args.order])
    print_packages_to_json(packages, args.json)
    if args.credits is not None:
        create_credits_file(
            packages,
            SORTORDER[args.order if args.order in [0, 1, 2] else 0],
            args.credits,
            args.creditstemplate)

    # Do the detection last. This means the json-file and credits file *will* be generated even of there
    # are packages with unwanted licenses.
    if detect_unwanted_licenses(packages, args.unwanted):
        print("Exiting with error level!")
        exit(1)


if __name__ == '__main__':
    main()
