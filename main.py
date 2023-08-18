"""

discover-release
--------------------------------------------------

Fargate task to move files from the embargo bucket to the public Discover bucket.
Once all files are moved, the files are deleted from the embargo bucket.

"""

import dataclasses
import json
import os
import threading
from dataclasses import dataclass
from multiprocessing.dummy import Pool
from typing import Any

import boto3
import structlog

ENVIRONMENT = os.environ["ENVIRONMENT"]
SERVICE_NAME = os.environ["SERVICE_NAME"]

LOCALSTACK_URL = "http://localstack:4566"


class EnhancedJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return super().default(o)


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


def release_files(s3_key_prefix, embargo_bucket, publish_bucket):
    # Ensure the S3 key ends with a '/'
    if not s3_key_prefix.endswith("/"):
        s3_key_prefix = "{}/".format(s3_key_prefix)

    assert s3_key_prefix.endswith("/")
    assert len(s3_key_prefix) > 1  # At least one character + slash

    # Create basic pennsieve log context
    log = structlog.get_logger()
    log = log.bind(**{"class": f"{release_files.__module__}.{release_files.__name__}"})
    log = log.bind(
        pennsieve={
            "service_name": SERVICE_NAME,
            "s3_key_prefix": s3_key_prefix,
            "publish_bucket": publish_bucket,
            "embargo_bucket": embargo_bucket,
        }
    )

    copy_results = []
    try:
        log.info("Starting thread pool")

        with Pool(processes=4) as pool:
            for copy_result in pool.imap_unordered(
                copy_object,
                (
                    CopyEvent(embargo_bucket, publish_bucket, key, log)
                    for key in iter_keys(embargo_bucket, s3_key_prefix)
                ),
            ):
                copy_results.append(copy_result)

            for _ in pool.imap_unordered(
                delete_object,
                (
                    DeleteEvent(embargo_bucket, key, log)
                    for key in iter_keys(embargo_bucket, s3_key_prefix)
                ),
            ):
                pass

    except Exception as e:
        log.error(e, exc_info=True)
        raise

    # serialize copy_results to JSON, and write to a file on S3
    log.info(f"generating copy result JSON ({len(copy_results)} files were copied)")
    json_data = bytes(json.dumps(copy_results, cls=EnhancedJSONEncoder), "utf-8")
    copy_results_key = f"{s3_key_prefix}discover-release-results.json"
    log.info(f"uploading copy results to s3://{publish_bucket}/{copy_results_key}")
    client = ThreadLocalS3Client(ENVIRONMENT)
    put_response = client.s3_client.put_object(
        Bucket=publish_bucket, Key=copy_results_key, Body=json_data
    )


def iter_keys(bucket, prefix):
    """
    Iterator over all keys in the embargo bucket under a key prefix.
    """
    pages = local.s3_client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket,
        Prefix=prefix,
        PaginationConfig={"PageSize": 1000},
        RequestPayer="requester",
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


@dataclass
class CopyResult:
    source_bucket: str
    source_key: str
    source_version: str
    target_bucket: str
    target_key: str
    target_version: str


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

    GB = 1024**3
    config = boto3.s3.transfer.TransferConfig(
        multipart_threshold=5 * GB, use_threads=False
    )

    # get source file attributes
    source_attr = {}
    try:
        source_attr = local.s3_client.get_object_attributes(
            Bucket=event.embargo_bucket, Key=event.key, ObjectAttributes=["ObjectParts"]
        )
    except AttributeError:
        source_attr = {}

    # copy file from source -> target
    local.s3_client.copy(
        {"Bucket": event.embargo_bucket, "Key": event.key},
        event.publish_bucket,
        event.key,
        Config=config,
        ExtraArgs={"RequestPayer": "requester"},
    )

    # get target file attributes
    target_attr = {}
    try:
        target_attr = local.s3_client.get_object_attributes(
            Bucket=event.publish_bucket, Key=event.key, ObjectAttributes=["ObjectParts"]
        )
    except AttributeError:
        target_attr = {}

    # generate CopyResult
    copy_result = CopyResult(
        event.embargo_bucket,
        event.key,
        source_attr["VersionId"] if "VersionId" in source_attr else "",
        event.publish_bucket,
        event.key,
        target_attr["VersionId"] if "VersionId" in target_attr else "",
    )
    return copy_result


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
    local.s3_client.delete_object(
        Bucket=event.embargo_bucket, Key=event.key, RequestPayer="requester"
    )


if __name__ == "__main__":
    s3_key_prefix = os.environ["S3_KEY_PREFIX"]
    publish_bucket = os.environ["PUBLISH_BUCKET"]
    embargo_bucket = os.environ["EMBARGO_BUCKET"]
    release_files(s3_key_prefix, embargo_bucket, publish_bucket)
