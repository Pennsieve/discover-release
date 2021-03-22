###############################################################################
# TARGET: build
###############################################################################

FROM python:3.8-slim-buster AS build

WORKDIR app

COPY requirements.txt ./

RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install -r requirements.txt && \
    find . -name "*.pyc" -delete

###############################################################################
# TARGET: service
###############################################################################

FROM build AS service

COPY main.py ./

CMD ["python3", "main.py"]

###############################################################################
# TARGET: test
###############################################################################

FROM build AS test

COPY requirements-test.txt ./

RUN python3 -m pip install -r requirements-test.txt

COPY main.py test.py test.txt ./
