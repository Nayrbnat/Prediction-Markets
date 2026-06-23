# Layer-cache-friendly build: extract declared dependencies from pyproject.toml
# and install them first (cached as long as pyproject.toml doesn't change),
# then copy the full source tree and install the local package.
FROM python:3.12-slim

WORKDIR /app

# 1. Copy only the packaging manifest so the dependency-install layer is cached
#    independently of application code changes.
COPY pyproject.toml ./

# 2. Install all declared dependencies without the local package source.
#    tomllib is in the standard library (3.11+) so no extra tooling is required.
#    This layer is re-used on code-only changes.
RUN python -c "\
import tomllib, subprocess, sys; \
data = tomllib.load(open('pyproject.toml', 'rb')); \
deps = data['project']['dependencies']; \
subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-cache-dir'] + deps)"

# 3. Copy the full source and install the local package without re-fetching deps.
COPY . .
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
