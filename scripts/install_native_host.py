"""Explicit administrator setup for the already enrolled student's browser profile."""
import argparse
from pathlib import Path
from agent.native_install import install

parser = argparse.ArgumentParser()
parser.add_argument('extension_id')
parser.add_argument('--data', required=True, type=Path)
parser.add_argument('--browser-instance', required=True)
parser.add_argument('--browser', choices=['edge', 'chrome'], default='edge')
parser.add_argument('--replace-binding', action='store_true')
args = parser.parse_args()
print(install(args.data, args.extension_id, args.browser_instance, args.browser, args.replace_binding))
