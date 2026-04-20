"""

discover-release
--------------------------------------------------

Fargate task to move files from the embargo bucket to the public Discover bucket.
Once all files are moved, the files are deleted from the embargo bucket.

Before the dataset manifest (manifest.json) is copied to the publish bucket, it
is rewritten so that each file entry's `s3VersionId` (and `sha256`, where
present) points at the values assigned by the publish bucket. The manifest's
own `size` is also patched to reflect the rewritten byte count. The release is
aborted if the manifest is missing so that embargo files can be left in place
for inspection and retry.

"""

import dataclasses
import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from multiprocessing.dummy import Pool
from typing import Any

import boto3
import structlog
from botocore.exceptions import ClientError

ENVIRONMENT = os.environ["ENVIRONMENT"]
SERVICE_NAME = os.environ["SERVICE_NAME"]

LOCALSTACK_URL = "http://localstack:4566"

KB = 1024**1
MB = 1024**2
GB = 1024**3

S3_COPY_OBJECT_MAX_SIZE = int(os.environ.get("S3_COPY_OBJECT_MAX_SIZE", 5 * GB))

MULTIPART_COPY_MAX_PART_SIZE = int(
    os.environ.get("MULTIPART_COPY_MAX_PART_SIZE", 5 * GB)
)

ChecksumAlgorithmSHA256 = "SHA256"
CHECKSUM_ALGORITHM = os.environ.get("CHECKSUM_ALGORITHM", ChecksumAlgorithmSHA256)

EmbargoResultRetentionDays = 180
EMBARGO_RESULT_RETENTION_DAYS = int(
    os.environ.get("EMBARGO_RESULT_RETENTION_DAYS", EmbargoResultRetentionDays)
)

# The dataset manifest is copied with modifications: its `files[].s3VersionId`
# (and `files[].sha256`, where present) entries are rewritten with the values
# assigned by the publish bucket after each file is copied, and its own
# `size` entry is patched to match the rewritten byte count.
MANIFEST_FILENAME = "manifest.json"

# Maximum iterations to converge on the manifest's self-referenced `size`
# field. Each pass changes the integer representation of `size` by at most
# one digit, so this converges in 2-3 iterations in practice.
MANIFEST_SIZE_MAX_ITERATIONS = 10


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
        self.copier_id = str(uuid.uuid4())

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
        return [response["result"] for response in responses]

    def copy_file(self, request):
        response = self.s3.copy_object(
            CopySource={"Bucket": request.source_bucket, "Key": request.source_key},
            Bucket=request.target_bucket,
            Key=request.target_key,
            ChecksumAlgorithm=request.checksum_algorithm,
            RequestPayer="requester",
        )
        return response

    def copy(self, request):
        self.logger = self.logger.bind(
            pennsieve={
                "copier_id": self.copier_id,
                "source_bucket": request.source_bucket,
                "source_key": request.source_key,
                "target_bucket": request.target_bucket,
                "target_key": request.target_key,
            }
        )

        source_attributes = self.get_object_attributes(
            request.source_bucket, request.source_key
        )

        if source_attributes.size <= S3_COPY_OBJECT_MAX_SIZE:
            self.logger.info(
                f"FileCopier.copy() performing single-operation copy (key: {request.source_key} size: {source_attributes.size})"
            )
            response = self.copy_file(request)
            self.logger.info(f"FileCopier.copy() response: {response}")
        else:
            self.logger.info(
                f"FileCopier.copy() performing multi-part copy (key: {request.source_key} size: {source_attributes.size})"
            )
            upload_id = self.start_multipart_operation(request)
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
            source_size=source_attributes.size,
            source_version_id=source_attributes.version_id,
            source_etag=source_attributes.etag,
            source_sha256=source_attributes.sha256,
            target_bucket=target_attributes.bucket,
            target_key=target_attributes.key,
            target_size=target_attributes.size,
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

    # Full S3 key of the dataset manifest, e.g. "10/manifest.json".
    manifest_key = f"{s3_key_prefix}{MANIFEST_FILENAME}"

    copy_results = []
    try:
        log.info("Starting thread pool")

        with Pool(processes=4) as pool:
            # Copy every file EXCEPT the dataset manifest. The manifest is
            # handled separately below so its s3VersionId/sha256 references
            # can be rewritten with the values assigned by the publish bucket.
            for copy_result in pool.imap_unordered(
                copy_object,
                (
                    CopyEvent(embargo_bucket, publish_bucket, key, log)
                    for key in iter_keys(embargo_bucket, s3_key_prefix)
                    if key != manifest_key
                ),
            ):
                copy_results.append(copy_result)

            # Rewrite manifest.json with the new version IDs / checksums and
            # upload the modified bytes to the publish bucket. If the manifest
            # is missing this raises FileNotFoundError, which propagates out
            # of the `try` BEFORE the delete pool runs, leaving embargo
            # untouched so the release can be inspected and retried.
            manifest_result = release_manifest(
                embargo_bucket=embargo_bucket,
                publish_bucket=publish_bucket,
                manifest_key=manifest_key,
                s3_key_prefix=s3_key_prefix,
                copy_results=copy_results,
                log=log,
            )
            copy_results.append(manifest_result)

            # Delete everything (including the original manifest) from embargo.
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
    log.info(
        f"uploading copy results to Publish bucket: s3://{publish_bucket}/{copy_results_key}"
    )
    client = ThreadLocalS3Client(ENVIRONMENT)
    put_response = client.s3_client.put_object(
        Bucket=publish_bucket,
        Key=copy_results_key,
        Body=json_data,
        RequestPayer="requester",
    )

    # the release results are uploaded to the Embargo Bucket for possible audit and recovery
    expiration = datetime.today() + timedelta(days=EMBARGO_RESULT_RETENTION_DAYS)
    log.info(
        f"uploading copy results to Embargo bucket: s3://{embargo_bucket}/{copy_results_key} (expires: {str(expiration)}"
    )
    put_response = client.s3_client.put_object(
        Bucket=embargo_bucket,
        Key=copy_results_key,
        Body=json_data,
        RequestPayer="requester",
        Expires=expiration,
    )


def release_manifest(
    embargo_bucket, publish_bucket, manifest_key, s3_key_prefix, copy_results, log
):
    """
    Download manifest.json from the embargo bucket and rewrite each file
    entry so that:

      * `s3VersionId` reflects the version ID assigned by the publish bucket
        for that file (taken from the `copy_results` produced by the copy
        pool).
      * `sha256` (when already present on the entry) reflects the SHA256
        checksum reported by the publish bucket for that file.

    The manifest's own entry in `files` is left without a fresh
    `s3VersionId` or `sha256` (a manifest cannot reference its own
    post-upload values), but its `size` IS patched to match the byte count
    of the rewritten manifest (converged iteratively because writing the
    size into the manifest changes the byte count).

    Raises FileNotFoundError if the manifest is missing under `manifest_key`,
    or if the manifest references files that were not copied (i.e. are not
    present in the embargo bucket). Either condition aborts the release
    without deleting anything from embargo so the operator can fix the
    dataset and retry.

    Returns a CopyResult describing the manifest upload.
    """
    s3 = local.s3_client

    try:
        response = s3.get_object(
            Bucket=embargo_bucket,
            Key=manifest_key,
            RequestPayer="requester",
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
            log.error(
                f"manifest.json not found at s3://{embargo_bucket}/{manifest_key}; aborting release"
            )
            raise FileNotFoundError(
                f"required manifest.json not found at s3://{embargo_bucket}/{manifest_key}"
            ) from e
        raise

    manifest = json.loads(response["Body"].read())

    # Manifest file paths are relative to the dataset root (e.g.
    # "files/sin_wave.edf") whereas S3 keys include the dataset prefix
    # (e.g. "10/files/sin_wave.edf"). Strip the prefix to build a
    # `relative_path -> CopyResult` map.
    result_by_path = {}
    for result in copy_results:
        if result.target_key.startswith(s3_key_prefix):
            relative_path = result.target_key[len(s3_key_prefix) :]
            result_by_path[relative_path] = result

    # Update s3VersionId and sha256 on each file entry. The manifest's own
    # entry is skipped here; its `size` is patched separately below.
    updated_version_ids = 0
    updated_sha256s = 0
    missing_paths = []
    manifest_self_entry = None
    for file_entry in manifest.get("files", []):
        path = file_entry.get("path")
        if path == MANIFEST_FILENAME:
            manifest_self_entry = file_entry
            continue
        if not path:
            continue
        result = result_by_path.get(path)
        if result is None:
            missing_paths.append(path)
            continue

        if result.target_version_id and result.target_version_id != "none":
            file_entry["s3VersionId"] = result.target_version_id
            updated_version_ids += 1

        # Only rewrite sha256 on entries that already have one. Adding it
        # to entries that didn't have it would change the manifest's
        # schema for those files, which is out of scope here.
        if "sha256" in file_entry:
            if result.target_sha256 and result.target_sha256 != "none":
                file_entry["sha256"] = result.target_sha256
                updated_sha256s += 1

    if missing_paths:
        preview = missing_paths[:5]
        suffix = "..." if len(missing_paths) > 5 else ""
        log.error(
            f"manifest.json references {len(missing_paths)} file(s) that are not present "
            f"in the embargo bucket under {s3_key_prefix}: {preview}{suffix}; aborting release"
        )
        raise FileNotFoundError(
            f"manifest.json references {len(missing_paths)} file(s) that are not present "
            f"in the embargo bucket under {s3_key_prefix}: {preview}{suffix}"
        )

    # Patch the manifest's own `size` so it matches the byte count of the
    # rewritten manifest. Setting `size` changes the byte count, so iterate
    # to a fixed point. The integer representation of `size` grows by at
    # most one digit per iteration, so this converges in 2-3 passes.
    if manifest_self_entry is not None:
        manifest_self_entry["size"] = 0
        modified_body = json.dumps(manifest, indent=2).encode("utf-8")
        for _ in range(MANIFEST_SIZE_MAX_ITERATIONS):
            actual_size = len(modified_body)
            if manifest_self_entry["size"] == actual_size:
                break
            manifest_self_entry["size"] = actual_size
            modified_body = json.dumps(manifest, indent=2).encode("utf-8")
        else:
            log.warning(
                f"manifest.json size did not converge after {MANIFEST_SIZE_MAX_ITERATIONS} "
                f"iterations: reported {manifest_self_entry['size']} vs actual {len(modified_body)}"
            )
    else:
        modified_body = json.dumps(manifest, indent=2).encode("utf-8")

    log.info(
        f"uploading modified manifest.json to s3://{publish_bucket}/{manifest_key} "
        f"(rewrote {updated_version_ids} s3VersionId, {updated_sha256s} sha256, "
        f"{len(modified_body)} bytes)"
    )

    s3.put_object(
        Bucket=publish_bucket,
        Key=manifest_key,
        Body=modified_body,
        ChecksumAlgorithm=CHECKSUM_ALGORITHM,
        RequestPayer="requester",
    )

    # Capture attributes for the release-results summary so the manifest
    # appears alongside every other file.
    source_attrs = local.file_copier.get_object_attributes(embargo_bucket, manifest_key)
    target_attrs = local.file_copier.get_object_attributes(publish_bucket, manifest_key)

    return CopyResult(
        source_bucket=source_attrs.bucket,
        source_key=source_attrs.key,
        source_size=source_attrs.size,
        source_version_id=source_attrs.version_id,
        source_etag=source_attrs.etag,
        source_sha256=source_attrs.sha256,
        target_bucket=target_attrs.bucket,
        target_key=target_attrs.key,
        target_size=target_attrs.size,
        target_version_id=target_attrs.version_id,
        target_etag=target_attrs.etag,
        target_sha256=target_attrs.sha256,
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

    event.log.info(f"Copy result: {copy_result}")
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
