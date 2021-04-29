.PHONY: dist clean setup check

dist:
	python -m build

clean:
	rmdir dist build

setup:
	pip install -r requirements.txt
	pip install -r dev-requirements.txt

check:
	pkglic/pkglic.py -f requirements.txt
	pkglic/pkglic.py -f sampledata/package.json