.PHONY: dist build clean setup check test testpublish publish

build dist: clean
	python -m build
	twine check dist/*

clean:
	rm -rf dist/ build/

setup:
	pip install -r requirements.txt -r dev-requirements.txt

test check:
	pkglic/pkglic.py -f requirements.txt
	pkglic/pkglic.py -f sampledata/package.json
	pkglic/pkglic.py -f sampledata/sample.csproj
	pkglic/pkglic.py -f sampledata/packages.config

publish: build
	twine upload $(REPO) dist/* --config-file .pypirc

testpublish: REPO=--repository testpypi
testpublish: publish
