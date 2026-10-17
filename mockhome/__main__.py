import sys

from fluffy.cli import main

if __name__ == "__main__":
    main(["home", *sys.argv[1:]])
