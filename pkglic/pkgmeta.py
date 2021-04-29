import os
import sys
import requests
import json
import argparse
import logging
import codecs
import concurrent.futures
from lxml import etree

SORTORDER = {
    0: lambda x: [x.type, x.name],
    1: lambda x: [x.license, x.name],
    2: lambda x: [x.type, x.license],
}

# PACKAGETYPE = ["python", "js", "cs"]


class PackageInfo:
    def __init__(self, name: str, version: str, filename: str):
        self.name = name
        self.version = version
        self.filename = filename
        self.license = "NOT_DOWNLOADED"
        self.licenseurl = ""


class NpmPackageInfo(PackageInfo):
    @property
    def url(self) -> str:
        return f"https://registry.npmjs.org/{self.name}/{self.version}"

    @property
    def type(self) -> str:
        return "js"

    def update_metadata(self, s: str) -> None:
        d = json.loads(s)
        self.license = d.get("license", "NOT_SET")


class PythonPackageInfo(PackageInfo):
    @property
    def url(self) -> str:
        if self.version is not None:
            return f"https://pypi.org/pypi/{self.name}/{self.version}/json"
        return f"https://pypi.org/pypi/{self.name}/json"

    @property
    def type(self) -> str:
        return "py"

    def update_metadata(self, s: str) -> None:
        d = json.loads(s)
        self.license = d.get("info", []).get("license", "NOT_SET")


class CSharpPackageInfo(PackageInfo):
    @property
    def url(self) -> str:
        # https://docs.microsoft.com/en-us/nuget/api/package-base-address-resource
        return f"https://api.nuget.org/v3-flatcontainer/{self.name}/{self.version}/{self.name}.nuspec".lower()

    @property
    def type(self) -> str:
        return "cs"

    def update_metadata(self, s: str) -> None:
        # https://docs.microsoft.com/en-us/nuget/reference/nuspec
        doc = etree.XML(s)
        nsmap = {}
        nsmap["nuget"] = doc.nsmap[None]
        self.licenseurl = doc.xpath("//nuget:licenseUrl/text()", namespaces=nsmap)[0]
        license = doc.xpath("//nuget:license", namespaces=nsmap)
        if len(license) > 0:
            lictype = license[0].xpath("./@type")[0]
            licvalue = license[0].xpath("./text()")[0]
            if lictype == "expression":
                self.license = licvalue
            else:
                self.license = f"{lictype}: {licvalue}"
        else:
            self.license = "NOT_SPECIFIED"


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
        logging.warning(f"{package_info.name} - not found at {package_info.url}")
        package_info.license = "404_NOT_FOUND"
        return

    package_info.update_metadata(r.content)


def fetch_package_infos(fetchinfo: list, verbose: bool):
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


def scan_python_deps(filename: str) -> list:
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
    for f in files:
        name, *version = f.split("==") + [None]
        fetchinfo.append(PythonPackageInfo(name, version[0], filename))
    return fetchinfo


def scan_npm_deps(filename: str):
    """
    Create information for all packages found in filename.
    Filename is expected to be in the package.json format.

    Args:
        filename (str): Name of file to read.

    Returns:
        list: List of PackageInfo instances.
    """
    dependencies = json.load(open(filename)).get("dependencies", [])
    return [NpmPackageInfo(name, version, filename) for name, version in dependencies.items()]


def scan_csharp_deps(filename: str):
    """
    Create information for all packages found in filename.
    Filename is expected to be in the *.csproj format.

    Args:
        filename (str): Name of file to read.

    Returns:
        list: List of PackageInfo instances.
    """
    csproj = codecs.open(filename, encoding='utf-8').read()
    doc = etree.XML(csproj)
    packagenode = doc.xpath("//PackageReference")
    return [CSharpPackageInfo(
                e.xpath("./@Include")[0],
                e.xpath("./@Version")[0],
                filename) for e in packagenode]


PACKAGETYPES = {
    "py": {"namefragment": "requirements.txt", "func": scan_python_deps},
    "js": {"namefragment": "package.json", "func": scan_npm_deps},
    "cs": {"namefragment": ".csproj", "func": scan_csharp_deps},
}


def init_argparse() -> argparse.ArgumentParser:
    """
    Initiate argument parser.
    """
    parser = argparse.ArgumentParser(fromfile_prefix_chars='@')
    parser.add_argument(
        '-f', "--files",
        metavar='path',
        type=str,
        nargs='+',
        required=True,
        help='input files to scan.')
    parser.add_argument(
        "-o", "--order",
        type=int,
        choices=SORTORDER.keys(),
        default=list(SORTORDER.keys())[0],
        help="Which fields to use to sort output; 0 - type, name, 1: license, name, 2: type, license.")
    parser.add_argument(
        "-t", "--type",
        type=str,
        choices=PACKAGETYPES.keys(),
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

    # Automatically add the parameter file args.txt if it exists.
    if os.path.exists("args.txt") and "@args.txt" not in sys.argv:
        sys.argv.append("@args.txt")

    return parser


def get_extractor_func(filename: str, default_type: str):
    """
    Determine the filetype of the file `filename`.

    Args:
        filename (str): Path to file.
        default_type (str): Default if not possible to determine type based on name.

    Returns:
        Returns a function that can parse the file type.
    """
    for packagetype in PACKAGETYPES.keys():
        if PACKAGETYPES[packagetype]["namefragment"] in filename:
            return PACKAGETYPES[packagetype]["func"]
    return PACKAGETYPES[default_type]["func"]


def acquire_package_info(filenames: list, default_type: str, verbose: bool) -> list:
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
        scan_func = get_extractor_func(filename, default_type)
        packages.extend(scan_func(filename))
    fetch_package_infos(packages, verbose)
    return packages


def print_package_info(packages: list, unwanted_licenses: list, sortkey) -> bool:
    """
    Print the package info ordered by the lambda function sortkey.
    If any package has a license found in unwanted_licenses the function returns False.

    Args:
        packages (list): List of PackageInfo instances
        unwanted_licenses (list): List of license names that trigger an error.
        sortkey: Lambda-function used to sort the output.

    Returns:
        bool: True if no unwanted licenses found.

    """
    unwanted = []
    for package in sorted(packages, key=sortkey):
        if package.license in unwanted_licenses:
            unwanted.append(package)
        print(f"[{package.type}] {package.name:20} {package.license} {package.licenseurl}")

    if unwanted:
        print("\nUnwanted license(s) detected!")
        for package in sorted(unwanted, key=sortkey):
            print(f"[{package.type}] '{package.name}' from {package.filename} uses unwanted license '{package.license}'.")
        return False
    return True


if __name__ == '__main__':
    print("pkgmeta - (c) Jesper Hogstrom 2021")
    parser = init_argparse()
    args = parser.parse_args()

    packages = acquire_package_info(args.files, args.type, args.verbose)
    if not print_package_info(packages, args.unwanted, SORTORDER[args.order]):
        print("Exiting with error level!")
        exit(1)
