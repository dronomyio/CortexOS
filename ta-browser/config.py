# config.py at the project source code root
import os
import glob

PROJECT_SOURCE_ROOT = os.path.dirname(os.path.abspath(__file__))
BASE_LOG_FOLDER = os.path.join(PROJECT_SOURCE_ROOT, 'log_files')
PROJECT_ROOT = os.path.dirname(PROJECT_SOURCE_ROOT)
PROJECT_TEMP_PATH = os.path.join(PROJECT_ROOT, 'temp')
