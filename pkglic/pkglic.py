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
from pprint import pprint
import authinfo

SORTORDER = {
    0: lambda x: [x.type(), x.name],
    1: lambda x: [x.license, x.name],
    2: lambda x: [x.type(), x.license],
    3: "g"
}

logger = logging.getLogger(authinfo.PROGRAM_NAME)
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
        name = f"{self.name} v{self.version}"
        props = []
        if self.licenseurl:
            props.append(self.licenseurl)
        if self.remapped:
            props.append(f"(remapped from '{self.orglicense}')")
        if self.whitelisted:
            props.append("(whitelisted)")
        return f"[{self.type()}] {name:30} {self.license} {' '.join(props)}".strip()

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

        self.license = d.get("license", "NOT_SPECIFIED")

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
        self.license = d.get("info", {}).get("license", "NOT_SPECIFIED")
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
                self.license = licvalue
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


def acquire_package_info(filenames: List[str], default_type: str, verbose: bool) -> List[PackageInfo]:
    """
    Download the metadata for all packages.

    Args:
        default_type (str): Default type if not possible to determine from file names.
        verbose (bool): Increase verbosity if True.

    Returns:
        list: List of PackageInfo instances.
    """
    packages = []
    for filename in filenames:
        parser = get_parser(filename, default_type)
        packages += parser.parse(filename)

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


def detect_unwanted_licenses(packages: List[PackageInfo], unwanted_licenses: List[str]) -> bool:
    """
    Detect if any of the licenses are 'unwanted' and if so print them.

    Args:
        packages (List[PackageInfo]): List of PackageInfo instances.
        unwanted_licenses (List[str]): List of licenses that are unwanted.

    Returns:
        bool: Returns true if there are unwanted licenses present
    """
    unwanted = [p for p in packages if p.license in unwanted_licenses and not p.whitelisted]
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
    parser = argparse.ArgumentParser(prog=authinfo.PROGRAM_NAME, fromfile_prefix_chars='@')
    parser.add_argument(
        '-f', "--files",
        metavar='file',
        type=str,
        nargs='+',
        required=True,
        help='input files to scan.')
    parser.add_argument(
        "-o", "--order",
        type=int,
        choices=SORTORDER.keys(),
        default=list(SORTORDER.keys())[0],
        help="Which fields to use to sort output; 0 - type, name, 1: license, name, 2: type, license, 3: group by license.")  # noqae501
    parser.add_argument(
        "-t", "--type",
        type=str,
        choices=[p.type() for p in PARSERS],
        default=None,
        help="Assume <type> for all <files> if not guessable.")
    parser.add_argument(
        "-u", "--unwanted",
        type=str,
        nargs='*',
        default=[],
        help="Exit with errorlevel on these license types.")
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Increase verbosity.")
    parser.add_argument(
        "--json",
        metavar="file",
        type=str,
        default=None,
        help="Output as json-string to <file>.")
    parser.add_argument(
        "-w", "--whitelist",
        metavar="file",
        type=str,
        default=None,
        help="Read whitelisted packages form <file>.")

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
        "generator": f"{authinfo.PROGRAM_NAME} {authinfo.VERSION} (c) {authinfo.AUTHOR} 2021",
        "generated": datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        "packages": [p.asjson() for p in packages]
    }

    with open(filename, "w") as f:
        f.write(json.dumps(res))
    print(f"\nJson-data written to '{filename}'.")


def get_whitelisted(filename: str) -> dict:
    if filename is None:
        logger.debug("No whitelist-file.")
        return None

    with open(filename) as f:
        try:
            return json.load(f)
        except json.decoder.JSONDecodeError:
            res = {}
            logger.debug(f"{filename} not in json format. Attempting line-by-line.")
            f.seek(0)
            for line in f.readlines():
                if line.strip().startswith("#"):
                    continue
                parts = line.split(":")
                print(parts)
                if len(parts) == 1:
                    parts.append("*")
                licmap = parts[1].split("->")
                if len(licmap) == 1:
                    licmap.append("")
                res[parts[0].strip()] = {"expect": licmap[0].strip(), "mapto": licmap[1].strip()}
            return res
        except Exception as e:
            logger.exception(e)
            return None


def apply_whitelisting(packages: List[PackageInfo], whitelisting: dict) -> List[PackageInfo]:
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


def main():
    print(f"{authinfo.PROGRAM_NAME} {authinfo.VERSION} - (c) {authinfo.AUTHOR} 2021.")
    print(f"Executed {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}.\n")
    parser = init_argparse()
    args = parser.parse_args()

    whitelisting = get_whitelisted(args.whitelist)
    pprint(whitelisting)

    packages = acquire_package_info(args.files, args.type, args.verbose)
    packages = apply_whitelisting(packages, whitelisting)
    print_package_info(packages, SORTORDER[args.order])
    if detect_unwanted_licenses(packages, args.unwanted):
        print("Exiting with error level!")
        exit(1)
    print_packages_to_json(packages, args.json)


if __name__ == '__main__':
    main()
