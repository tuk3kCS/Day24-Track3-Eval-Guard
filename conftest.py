"""Root conftest.py – ensures the project root is on sys.path for all tests."""
import sys
import os

# Add the project root so that `from src.xxx import ...` works in all tests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
