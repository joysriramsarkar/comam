"""Unit/API tests must be hermetic: a real MODEL_API_KEY in the developer's
environment must not switch /api/command onto the live Spark path and make
results depend on the network. The live check lives in ../../check_api.py."""
import os

os.environ["TESTING"] = "1"
os.environ.pop("MODEL_API_KEY", None)
os.environ["MODEL_API_KEY"] = ""
