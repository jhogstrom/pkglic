.PHONY: dist clean setup check testpublish publish

dist:
	python -m build

clean:
	rmdir dist build

setup:
	pip install -r requirements.txt -r dev-requirements.txt

check:
	pkglic/pkglic.py -f requirements.txt
	pkglic/pkglic.py -f sampledata/package.json
	pkglic/pkglic.py -f sampledata/sample.csproj
	pkglic/pkglic.py -f sampledata/packages.config

publish:
	twine upload $(REPO) dist/* --config-file .pypirc

testpublish: REPO=--repository testpypi
testpublish: publish
