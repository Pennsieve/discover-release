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
import uuid
from dataclasses import dataclass
from multiprocessing.dummy import Pool
from typing import Any
from datetime import date, datetime, timedelta

import boto3
import structlog

ENVIRONMENT = os.environ["ENVIRONMENT"]
SERVICE_NAME = os.environ["SERVICE_NAME"]

LOCALSTACK_URL = "http://localstack:4566"

KB = 1024**1
MB = 1024**2
GB = 1024**3

MULTIPART_COPY_MAX_PART_SIZE = int(
    os.environ.get("MULTIPART_COPY_MAX_PART_SIZE", 5 * GB)
)

ChecksumAlgorithmSHA256 = "SHA256"
CHECKSUM_ALGORITHM = os.environ.get("CHECKSUM_ALGORITHM", ChecksumAlgorithmSHA256)

EmbargoResultRetentionDays = 180
EMBARGO_RESULT_RETENTION_DAYS = int(os.environ.get("EMBARGO_RESULT_RETENTION_DAYS", EmbargoResultRetentionDays))

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


@dataclass
class ObjectAttributes:
    bucket: str
    key: str
    size: int
    version_id: str
    etag: str
    sha256: str


@dataclass
class CopyEvent:
    embargo_bucket: str
    publish_bucket: str
    key: str
    log: Any


@dataclass
class DeleteEvent:
    embargo_bucket: str
    key: str
    log: Any


@dataclass
class CopyRequest:
    source_bucket: str
    source_key: str
    target_bucket: str
    target_key: str
    max_part_size: int
    checksum_algorithm: str


@dataclass
class CopyResult:
    source_bucket: str
    source_key: str
    source_size: int
    source_version_id: str
    source_etag: str
    source_sha256: str
    target_bucket: str
    target_key: str
    target_size: int
    target_version_id: str
    target_etag: str
    target_sha256: str


class FileCopier:
    def __init__(self, logger, s3, max_part_size=5 * MB):
        self.logger = logger
        self.s3 = s3
        self.max_part_size = max_part_size

    def get_object_attributes(self, bucket, key):
        response = self.s3.get_object_attributes(
            Bucket=bucket,
            Key=key,
            ObjectAttributes=["ObjectSize", "ETag", "Checksum"],
            RequestPayer="requester",
        )
        # print(f"s3.get_object_attributes() response: {response}")
        return ObjectAttributes(
            bucket=bucket,
            key=key,
            size=response.get("ObjectSize", 0),
            version_id=response.get("VersionId", "none"),
            etag=response.get("ETag", "none"),
            sha256=response.get("Checksum", {}).get("ChecksumSHA256", "none"),
        )

    def start_multipart_operation(self, request):
        # initiate multipart upload
        response = self.s3.create_multipart_upload(
            Bucket=request.target_bucket,
            Key=request.target_key,
            ChecksumAlgorithm=request.checksum_algorithm,
            RequestPayer="requester",
        )
        # print(f"s3.create_multipart_upload() response: {response}")
        return response["UploadId"]

    def finish_multipart_operation(self, request, upload_id, parts):
        # complete multipart upload
        response = self.s3.complete_multipart_upload(
            Bucket=request.target_bucket,
            Key=request.target_key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
            RequestPayer="requester",
        )
        # print(f"s3.complete_multipart_upload() response: {response}")
        return response

    def byte_range(self, offset, size):
        return f"bytes={offset}-{offset+size-1}"

    def generate_part_list(self, object_size, max_part_size):
        parts = []
        offset = 0
        while offset < object_size:
            remaining = object_size - offset
            if remaining >= max_part_size:
                parts.append(self.byte_range(offset, max_part_size))
                offset += max_part_size
            else:
                parts.append(self.byte_range(offset, remaining))
                offset += remaining
        return parts

    def copy_part(self, request, upload_id, part_number, part_range):
        response = self.s3.upload_part_copy(
            Bucket=request.target_bucket,
            Key=request.target_key,
            CopySource={"Bucket": request.source_bucket, "Key": request.source_key},
            UploadId=upload_id,
            CopySourceRange=part_range,
            PartNumber=part_number,
            RequestPayer="requester",
        )
        # print(f"s3.upload_part_copy() response: {response}")
        return response

    def copy_parts(self, request, upload_id, parts):
        part_number = 0
        responses = []
        for part_range in parts:
            part_number += 1
            response = self.copy_part(request, upload_id, part_number, part_range)
            result = response["CopyPartResult"]
            result["PartNumber"] = part_number
            del result["LastModified"]
            responses.append(
                {
                    "part_number": part_number,
                    "part_range": part_range,
                    "response": response,
                    "result": result,
                }
            )
            # print(f"copy_parts() result: ${result}")
        return [response["result"] for response in responses]

    def copy(self, request):
        self.logger = self.logger.bind(
            pennsieve={
                "source_bucket": request.source_bucket,
                "source_key": request.source_key,
                "target_bucket": request.target_bucket,
                "target_key": request.target_key,
            }
        )
        upload_id = self.start_multipart_operation(request)
        source_attributes = self.get_object_attributes(
            request.source_bucket, request.source_key
        )
        parts = self.generate_part_list(source_attributes.size, self.max_part_size)
        self.logger.info(f"FileCopier.copy() number-of-parts: {len(parts)}")
        copied_parts = self.copy_parts(request, upload_id, parts)
        response = self.finish_multipart_operation(request, upload_id, copied_parts)
        self.logger.info(f"FileCopier.copy() response: {response}")
        target_attributes = self.get_object_attributes(
            request.target_bucket, request.target_key
        )
        return CopyResult(
            source_bucket=source_attributes.bucket,
            source_key=source_attributes.key,
            source_size=str(source_attributes.size),
            source_version_id=source_attributes.version_id,
            source_etag=source_attributes.etag,
            source_sha256=source_attributes.sha256,
            target_bucket=target_attributes.bucket,
            target_key=target_attributes.key,
            target_size=str(target_attributes.size),
            target_version_id=target_attributes.version_id,
            target_etag=target_attributes.etag,
            target_sha256=target_attributes.sha256,
        )


# Configure S3 client
# --------------------------------------------------


class ThreadLocalS3Client(threading.local):
    """
    Boto clients are not thread safe, so each thread needs a local instance
    """

    def __init__(self, environment):
        self.local_id = str(uuid.uuid4())
        self.logger = structlog.get_logger()

        if environment == "local":
            s3_url = LOCALSTACK_URL
        else:
            s3_url = None

        print("Creating S3 client...")
        self.s3_client = boto3.client("s3", endpoint_url=s3_url)
        self.file_copier = FileCopier(
            self.logger, self.s3_client, MULTIPART_COPY_MAX_PART_SIZE
        )


local = ThreadLocalS3Client(ENVIRONMENT)


# Main handler
# --------------------------------------------------


def release_files(request_id, s3_key_prefix, embargo_bucket, publish_bucket):
    # Ensure the S3 key ends with a '/'
    if not s3_key_prefix.endswith("/"):
        s3_key_prefix = "{}/".format(s3_key_prefix)

    assert s3_key_prefix.endswith("/")
    assert len(s3_key_prefix) > 1  # At least one character + slash

    delete_released_files = os.environ.get()

    # Create basic pennsieve log context
    log = structlog.get_logger()
    log = log.bind(**{"class": f"{release_files.__module__}.{release_files.__name__}"})
    log = log.bind(
        pennsieve={
            "service_name": SERVICE_NAME,
            "request_id": request_id,
            "s3_key_prefix": s3_key_prefix,
            "publish_bucket": publish_bucket,
            "embargo_bucket": embargo_bucket,
        }
    )

    log.info(f"boto3 version: {boto3.__version__}")

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

    log.info(f"generating copy result JSON ({len(copy_results)} files were copied)")
    json_data = bytes(json.dumps(copy_results, cls=EnhancedJSONEncoder), "utf-8")
    copy_results_key = f"{s3_key_prefix}discover-release-results.json"

    # the release results are uploaded to the Publish Bucket for the Discover Service to consume
    log.info(f"uploading copy results to Publish bucket: s3://{publish_bucket}/{copy_results_key}")
    client = ThreadLocalS3Client(ENVIRONMENT)
    put_response = client.s3_client.put_object(
        Bucket=publish_bucket,
        Key=copy_results_key,
        Body=json_data,
        RequestPayer="requester",
    )

    # the release results are uploaded to the Embargo Bucket for possible audit and recovery
    expiration = date.today() + timedelta(days = EMBARGO_RESULT_RETENTION_DAYS)
    log.info(f"uploading copy results to Embargo bucket: s3://{embargo_bucket}/{copy_results_key} (expires: {str(expiration)}")
    put_response = client.s3_client.put_object(
        Bucket=embargo_bucket,
        Key=copy_results_key,
        Body=json_data,
        RequestPayer="requester",
        Expires=expiration
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

    copy_result = local.file_copier.copy(
        CopyRequest(
            source_bucket=event.embargo_bucket,
            source_key=event.key,
            target_bucket=event.publish_bucket,
            target_key=event.key,
            max_part_size=MULTIPART_COPY_MAX_PART_SIZE,
            checksum_algorithm=CHECKSUM_ALGORITHM,
        )
    )

    event.log.info(f"Copy result: ${copy_result}")
    return copy_result


def delete_object(event: DeleteEvent):
    """
    Delete an object from the embargo bucket.
    """
    event.log.info(f"Deleting s3://{event.embargo_bucket}/{event.key}")
    local.s3_client.delete_object(
        Bucket=event.embargo_bucket, Key=event.key, RequestPayer="requester"
    )


if __name__ == "__main__":
    request_id = str(uuid.uuid4())
    s3_key_prefix = os.environ["S3_KEY_PREFIX"]
    publish_bucket = os.environ["PUBLISH_BUCKET"]
    embargo_bucket = os.environ["EMBARGO_BUCKET"]
    release_files(request_id, s3_key_prefix, embargo_bucket, publish_bucket)
