import os
import time
import uuid

import boto3
import pytest

from main import LOCALSTACK_URL, release_files

PUBLISH_BUCKET = "test-publish-bucket"
EMBARGO_BUCKET = "test-embargo-bucket"

# This key corresponds to assets belonging to a dataset in the embargo bucket
# that needs to be released to the public bucket.
S3_PREFIX_TO_MOVE = "1/10/"

# This key corresponds to assets belonging to a dataset version
# that should remain untouched by this lambda function
S3_PREFIX_TO_LEAVE = "1/100/"

# This is a dummy file
FILENAME = "test.txt"

s3_resource = boto3.resource("s3", endpoint_url=LOCALSTACK_URL)


@pytest.fixture(scope="module")
def setup():
    os.environ.update(
        {
            "PUBLISH_BUCKET": PUBLISH_BUCKET,
            "EMBARGO_BUCKET": EMBARGO_BUCKET,
        }
    )

    for _ in range(10):
        try:
            list(s3_resource.buckets.all())
            print("Localstack running")
            break
        except:
            print("Waiting for Localstack to start...")
            time.sleep(1)
    else:
        raise Exception("Localstack did not start.")


@pytest.fixture(scope="function")
def publish_bucket(setup):
    return setup_bucket(PUBLISH_BUCKET)


@pytest.fixture(scope="function")
def embargo_bucket(setup):
    return setup_bucket(EMBARGO_BUCKET)


def test_copy_files_to_publish_bucket(publish_bucket, embargo_bucket):
    s3_key_to_move = os.path.join(S3_PREFIX_TO_MOVE, FILENAME)
    s3_key_to_leave = os.path.join(S3_PREFIX_TO_LEAVE, FILENAME)

    embargo_bucket.upload_file(Filename=FILENAME, Key=s3_key_to_move)
    embargo_bucket.upload_file(Filename=FILENAME, Key=s3_key_to_leave)

    assert sorted(s3_keys(publish_bucket)) == []
    assert sorted(s3_keys(embargo_bucket)) == sorted([s3_key_to_move, s3_key_to_leave])

    request_id = str(uuid.uuid4())
    release_files(request_id, S3_PREFIX_TO_MOVE, EMBARGO_BUCKET, PUBLISH_BUCKET)

    # VERIFY RESULTS
    release_results_key = os.path.join(
        S3_PREFIX_TO_MOVE, "discover-release-results.json"
    )
    assert sorted(s3_keys(publish_bucket)) == sorted(
        [s3_key_to_move, release_results_key]
    )
    assert sorted(s3_keys(embargo_bucket)) == sorted(
        [s3_key_to_leave, release_results_key]
    )


def test_handle_key_without_trailing_slash(publish_bucket, embargo_bucket):
    s3_key_to_move = os.path.join(S3_PREFIX_TO_MOVE, FILENAME)
    s3_key_to_leave = os.path.join(S3_PREFIX_TO_LEAVE, FILENAME)

    embargo_bucket.upload_file(Filename=FILENAME, Key=s3_key_to_move)
    embargo_bucket.upload_file(Filename=FILENAME, Key=s3_key_to_leave)

    assert sorted(s3_keys(publish_bucket)) == []
    assert sorted(s3_keys(embargo_bucket)) == sorted([s3_key_to_move, s3_key_to_leave])

    request_id = str(uuid.uuid4())
    release_files(request_id, S3_PREFIX_TO_MOVE, EMBARGO_BUCKET, PUBLISH_BUCKET)

    # VERIFY RESULTS
    release_results_key = os.path.join(
        S3_PREFIX_TO_MOVE, "discover-release-results.json"
    )

    assert sorted(s3_keys(publish_bucket)) == sorted(
        [s3_key_to_move, release_results_key]
    )
    assert sorted(s3_keys(embargo_bucket)) == sorted(
        [s3_key_to_leave, release_results_key]
    )


def test_copy_files_pagination(publish_bucket, embargo_bucket):
    # More keys than the S3 page size
    s3_keys_to_move = create_keys(S3_PREFIX_TO_MOVE, FILENAME, 1200)
    s3_keys_to_leave = [os.path.join(S3_PREFIX_TO_LEAVE, FILENAME)]

    for key in s3_keys_to_move:
        embargo_bucket.upload_file(Filename=FILENAME, Key=key)

    for key in s3_keys_to_leave:
        embargo_bucket.upload_file(Filename=FILENAME, Key=key)

    assert sorted(s3_keys(publish_bucket)) == []
    assert sorted(s3_keys(embargo_bucket)) == sorted(s3_keys_to_move + s3_keys_to_leave)

    request_id = str(uuid.uuid4())
    release_files(request_id, S3_PREFIX_TO_MOVE, EMBARGO_BUCKET, PUBLISH_BUCKET)

    # VERIFY RESULTS
    release_results_key = os.path.join(
        S3_PREFIX_TO_MOVE, "discover-release-results.json"
    )
    s3_keys_to_move.append(release_results_key)
    assert sorted(s3_keys(publish_bucket)) == sorted(s3_keys_to_move)
    assert sorted(s3_keys(embargo_bucket)) == sorted(
        s3_keys_to_leave + [release_results_key]
    )


def test_embargo_bucket_only_contains_release_results(publish_bucket, embargo_bucket):
    s3_keys_to_move = create_keys(S3_PREFIX_TO_MOVE, FILENAME, 25)

    for key in s3_keys_to_move:
        embargo_bucket.upload_file(Filename=FILENAME, Key=key)

    assert sorted(s3_keys(publish_bucket)) == []
    assert sorted(s3_keys(embargo_bucket)) == sorted(s3_keys_to_move)

    request_id = str(uuid.uuid4())
    release_files(request_id, S3_PREFIX_TO_MOVE, EMBARGO_BUCKET, PUBLISH_BUCKET)

    # VERIFY RESULTS
    release_results_key = os.path.join(
        S3_PREFIX_TO_MOVE, "discover-release-results.json"
    )
    s3_keys_to_move.append(release_results_key)

    assert sorted(s3_keys(publish_bucket)) == sorted(s3_keys_to_move)
    assert sorted(s3_keys(embargo_bucket)) == [release_results_key]


def setup_bucket(bucket_name):
    s3_resource.create_bucket(Bucket=bucket_name)
    bucket = s3_resource.Bucket(bucket_name)
    bucket.objects.all().delete()
    return bucket


def s3_keys(bucket):
    return [obj.key for obj in bucket.objects.all()]


def create_keys(prefix, filename, n):
    return ["{}/{}{}".format(prefix, i, filename) for i in range(n)]
