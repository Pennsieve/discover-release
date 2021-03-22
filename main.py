"""

discover-release
--------------------------------------------------

Fargate task to move files from the embargo bucket to the public Discover bucket.
Once all files are moved, the files are deleted from the embargo bucket.

"""

import os
import threading
from dataclasses import dataclass

# Thread-based multiprocessing module
from multiprocessing.dummy import Pool
from typing import Any

import boto3
import structlog

ENVIRONMENT = os.environ["ENVIRONMENT"]
SERVICE_NAME = os.environ["SERVICE_NAME"]
PUBLISH_BUCKET = os.environ["PUBLISH_BUCKET"]
EMBARGO_BUCKET = os.environ["EMBARGO_BUCKET"]

LOCALSTACK_URL = "http://localstack:4572"

# Configure JSON logs in a format that ELK can understand
# --------------------------------------------------


def rewrite_event_to_message(logger, name, event_dict):
    """
    Rewrite the default structlog `event` to a `message`.
    """
    event = event_dict.pop("event", None)
    if event is not None:
        event_dict["message"] = event
    return event_dict


def add_log_level(logger, name, event_dict):
    event_dict["log_level"] = name.upper()
    return event_dict


structlog.configure(
    processors=[
        rewrite_event_to_message,
        add_log_level,
        structlog.processors.format_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

# Configure S3 client
# --------------------------------------------------


class ThreadLocalS3Client(threading.local):
    """
    Boto clients are not thread safe, so each thread needs a local instance
    """

    def __init__(self, environment):
        if environment == "local":
            s3_url = LOCALSTACK_URL
        else:
            s3_url = None

        print("Creating S3 client...")
        self.s3_client = boto3.client("s3", endpoint_url=s3_url)


local = ThreadLocalS3Client(ENVIRONMENT)


# Main handler
# --------------------------------------------------


def release_files(s3_key_prefix):

    # Ensure the S3 key ends with a '/'
    if not s3_key_prefix.endswith("/"):
        s3_key_prefix = "{}/".format(s3_key_prefix)

    assert s3_key_prefix.endswith("/")
    assert len(s3_key_prefix) > 1  # At least one character + slash

    # Create basic pennsieve log context
    log = structlog.get_logger()
    log = log.bind(**{"class": f"{release_files.__module__}.{release_files.__name__}"})
    log = log.bind(
        pennsieve={"service_name": SERVICE_NAME, "s3_key_prefix": s3_key_prefix}
    )

    try:
        log.info("Starting thread pool")

        with Pool(processes=4) as pool:
            for _ in pool.imap_unordered(
                copy_object,
                (
                    CopyEvent(EMBARGO_BUCKET, PUBLISH_BUCKET, key, log)
                    for key in iter_keys(EMBARGO_BUCKET, s3_key_prefix)
                ),
            ):
                pass

            for _ in pool.imap_unordered(
                delete_object,
                (
                    DeleteEvent(EMBARGO_BUCKET, key, log)
                    for key in iter_keys(EMBARGO_BUCKET, s3_key_prefix)
                ),
            ):
                pass

    except Exception as e:
        log.error(e, exc_info=True)
        raise


def iter_keys(bucket, prefix):
    """
    Iterator over all keys in the embargo bucket under a key prefix.
    """
    pages = local.s3_client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix, PaginationConfig={"PageSize": 1000}
    )

    for page in pages:
        if "Contents" in page:
            for item in page["Contents"]:
                yield item["Key"]


@dataclass
class CopyEvent:
    embargo_bucket: str
    publish_bucket: str
    key: str
    log: Any


def copy_object(event: CopyEvent):
    """
    Copy an object from the embargo bucket to the release bucket.

    This requires two small S3 config tweaks:

    1. Only use multipart if the file is actually larger than the max threshold
    2. Don't use threads: we are already parallelized at the file level
    """
    event.log.info(
        f"Copying s3://{event.embargo_bucket}/{event.key} to s3://{event.publish_bucket}/{event.key}"
    )

    GB = 1024 ** 3
    config = boto3.s3.transfer.TransferConfig(
        multipart_threshold=5 * GB, use_threads=False
    )

    local.s3_client.copy(
        {"Bucket": event.embargo_bucket, "Key": event.key},
        event.publish_bucket,
        event.key,
        Config=config,
    )


@dataclass
class DeleteEvent:
    embargo_bucket: str
    key: str
    log: Any


def delete_object(event: DeleteEvent):
    """
    Delete an object from the embargo bucket.
    """
    event.log.info(f"Deleting s3://{event.embargo_bucket}/{event.key}")
    local.s3_client.delete_object(Bucket=event.embargo_bucket, Key=event.key)


if __name__ == "__main__":
    s3_key_prefix = os.environ["S3_KEY_PREFIX"]
    release_files(s3_key_prefix)
