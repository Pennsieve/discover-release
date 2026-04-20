import json
import os
import time
import uuid

import boto3
import pytest

from main import LOCALSTACK_URL, release_files

PUBLISH_BUCKET = "test-publish-bucket"
EMBARGO_BUCKET = "test-embargo-bucket"

# Prefix of the dataset being released. The current convention is
# `<dataset-id>/`.
S3_PREFIX_TO_MOVE = "10/"

# Prefix of an unrelated dataset that should remain untouched by the release.
S3_PREFIX_TO_LEAVE = "100/"

# This is a dummy file
FILENAME = "test.txt"

MANIFEST_RELATIVE_PATH = "manifest.json"

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
    return setup_bucket(PUBLISH_BUCKET, versioned=True)


@pytest.fixture(scope="function")
def embargo_bucket(setup):
    return setup_bucket(EMBARGO_BUCKET, versioned=False)


def test_copy_files_to_publish_bucket(publish_bucket, embargo_bucket):
    s3_key_to_move = os.path.join(S3_PREFIX_TO_MOVE, FILENAME)
    s3_key_to_leave = os.path.join(S3_PREFIX_TO_LEAVE, FILENAME)

    embargo_bucket.upload_file(Filename=FILENAME, Key=s3_key_to_move)
    embargo_bucket.upload_file(Filename=FILENAME, Key=s3_key_to_leave)
    manifest_key, _ = upload_manifest(embargo_bucket, S3_PREFIX_TO_MOVE, [FILENAME])

    assert sorted(s3_keys(publish_bucket)) == []
    assert sorted(s3_keys(embargo_bucket)) == sorted(
        [s3_key_to_move, s3_key_to_leave, manifest_key]
    )

    request_id = str(uuid.uuid4())
    release_files(request_id, S3_PREFIX_TO_MOVE, EMBARGO_BUCKET, PUBLISH_BUCKET)

    # VERIFY RESULTS
    release_results_key = os.path.join(
        S3_PREFIX_TO_MOVE, "discover-release-results.json"
    )
    assert sorted(s3_keys(publish_bucket)) == sorted(
        [s3_key_to_move, manifest_key, release_results_key]
    )
    assert sorted(s3_keys(embargo_bucket)) == sorted(
        [s3_key_to_leave, release_results_key]
    )


def test_handle_key_without_trailing_slash(publish_bucket, embargo_bucket):
    s3_key_to_move = os.path.join(S3_PREFIX_TO_MOVE, FILENAME)
    s3_key_to_leave = os.path.join(S3_PREFIX_TO_LEAVE, FILENAME)

    embargo_bucket.upload_file(Filename=FILENAME, Key=s3_key_to_move)
    embargo_bucket.upload_file(Filename=FILENAME, Key=s3_key_to_leave)
    manifest_key, _ = upload_manifest(embargo_bucket, S3_PREFIX_TO_MOVE, [FILENAME])

    assert sorted(s3_keys(publish_bucket)) == []
    assert sorted(s3_keys(embargo_bucket)) == sorted(
        [s3_key_to_move, s3_key_to_leave, manifest_key]
    )

    # Pass the prefix without a trailing slash; release_files should normalize it.
    prefix_no_slash = S3_PREFIX_TO_MOVE.rstrip("/")
    request_id = str(uuid.uuid4())
    release_files(request_id, prefix_no_slash, EMBARGO_BUCKET, PUBLISH_BUCKET)

    # VERIFY RESULTS
    release_results_key = os.path.join(
        S3_PREFIX_TO_MOVE, "discover-release-results.json"
    )
    assert sorted(s3_keys(publish_bucket)) == sorted(
        [s3_key_to_move, manifest_key, release_results_key]
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

    # The manifest only needs to exist; it doesn't have to enumerate every
    # file in S3 for the release to succeed.
    manifest_key, _ = upload_manifest(embargo_bucket, S3_PREFIX_TO_MOVE, [])

    assert sorted(s3_keys(publish_bucket)) == []
    assert sorted(s3_keys(embargo_bucket)) == sorted(
        s3_keys_to_move + s3_keys_to_leave + [manifest_key]
    )

    request_id = str(uuid.uuid4())
    release_files(request_id, S3_PREFIX_TO_MOVE, EMBARGO_BUCKET, PUBLISH_BUCKET)

    # VERIFY RESULTS
    release_results_key = os.path.join(
        S3_PREFIX_TO_MOVE, "discover-release-results.json"
    )
    expected_in_publish = s3_keys_to_move + [manifest_key, release_results_key]
    assert sorted(s3_keys(publish_bucket)) == sorted(expected_in_publish)
    assert sorted(s3_keys(embargo_bucket)) == sorted(
        s3_keys_to_leave + [release_results_key]
    )


def test_embargo_bucket_only_contains_release_results(publish_bucket, embargo_bucket):
    s3_keys_to_move = create_keys(S3_PREFIX_TO_MOVE, FILENAME, 25)

    for key in s3_keys_to_move:
        embargo_bucket.upload_file(Filename=FILENAME, Key=key)

    manifest_key, _ = upload_manifest(embargo_bucket, S3_PREFIX_TO_MOVE, [])

    assert sorted(s3_keys(publish_bucket)) == []
    assert sorted(s3_keys(embargo_bucket)) == sorted(s3_keys_to_move + [manifest_key])

    request_id = str(uuid.uuid4())
    release_files(request_id, S3_PREFIX_TO_MOVE, EMBARGO_BUCKET, PUBLISH_BUCKET)

    # VERIFY RESULTS
    release_results_key = os.path.join(
        S3_PREFIX_TO_MOVE, "discover-release-results.json"
    )
    expected_in_publish = s3_keys_to_move + [manifest_key, release_results_key]

    assert sorted(s3_keys(publish_bucket)) == sorted(expected_in_publish)
    assert sorted(s3_keys(embargo_bucket)) == [release_results_key]


def test_manifest_is_rewritten_with_publish_bucket_values(
    publish_bucket, embargo_bucket
):
    """
    manifest.json should be rewritten so each file entry's s3VersionId points
    at the version ID assigned by the publish bucket; entries that already
    have a sha256 should be rewritten with the publish-bucket SHA256; the
    manifest's own entry should not get an s3VersionId or sha256 but its
    `size` should match the rewritten byte count.
    """

    paths_with_sha256 = ["files/ps_client.py", "files/requirements.txt"]
    paths_without_sha256 = ["banner.jpg", "readme.md"]
    relative_paths = paths_with_sha256 + paths_without_sha256

    for rel_path in relative_paths:
        embargo_bucket.upload_file(
            Filename=FILENAME, Key=os.path.join(S3_PREFIX_TO_MOVE, rel_path)
        )

    manifest_key, original_manifest = upload_manifest(
        embargo_bucket,
        S3_PREFIX_TO_MOVE,
        relative_paths,
        with_sha256=paths_with_sha256,
    )

    request_id = str(uuid.uuid4())
    release_files(request_id, S3_PREFIX_TO_MOVE, EMBARGO_BUCKET, PUBLISH_BUCKET)

    # Read the manifest out of the publish bucket
    published_body = (
        s3_resource.Object(PUBLISH_BUCKET, manifest_key).get()["Body"].read()
    )
    published_manifest = json.loads(published_body)
    by_path = {entry["path"]: entry for entry in published_manifest["files"]}

    # The manifest's own entry should not carry s3VersionId or sha256...
    assert "s3VersionId" not in by_path[MANIFEST_RELATIVE_PATH]
    assert "sha256" not in by_path[MANIFEST_RELATIVE_PATH]
    # ...but its `size` should match the rewritten manifest's byte count.
    assert by_path[MANIFEST_RELATIVE_PATH]["size"] == len(published_body)

    # Every referenced file should have a fresh, non-empty version ID that
    # differs from the stale one seeded above.
    for rel_path in relative_paths:
        entry = by_path[rel_path]
        assert "s3VersionId" in entry, f"{rel_path} missing s3VersionId"
        assert entry["s3VersionId"], f"{rel_path} has empty s3VersionId"
        assert not entry["s3VersionId"].startswith(
            "stale-version-"
        ), f"{rel_path} still has the stale embargo version ID"

    # Files that had a sha256 in the original manifest should have a fresh,
    # non-stale value. Files that did NOT have a sha256 should still not
    # have one (we don't add the field if it wasn't already there).
    for rel_path in paths_with_sha256:
        entry = by_path[rel_path]
        assert "sha256" in entry, f"{rel_path} should retain its sha256 field"
        assert entry["sha256"], f"{rel_path} has empty sha256"
        assert not entry["sha256"].startswith(
            "stale-sha256-"
        ), f"{rel_path} still has the stale embargo sha256"

    for rel_path in paths_without_sha256:
        assert "sha256" not in by_path[rel_path], (
            f"{rel_path} unexpectedly gained a sha256 field"
        )

    # Fields that the rewrite is NOT supposed to touch (name, path, size,
    # fileType, sourcePackageId, and anything else the manifest happens to
    # carry) must come through unchanged. Compare against the original
    # manifest entry by entry so a future refactor that rebuilds entries
    # from scratch and drops a field will fail this test.
    original_by_path = {entry["path"]: entry for entry in original_manifest["files"]}
    pass_through_fields = ("name", "path", "size", "fileType", "sourcePackageId")
    for rel_path in relative_paths:
        original = original_by_path[rel_path]
        updated = by_path[rel_path]
        for field in pass_through_fields:
            if field in original:
                assert field in updated, (
                    f"{rel_path}: pass-through field {field!r} was dropped"
                )
                assert updated[field] == original[field], (
                    f"{rel_path}: pass-through field {field!r} changed from "
                    f"{original[field]!r} to {updated[field]!r}"
                )

    # And the embargo bucket's copy of the manifest should be gone.
    assert manifest_key not in s3_keys(embargo_bucket)


def test_release_aborts_when_manifest_missing(publish_bucket, embargo_bucket):
    """
    When manifest.json is absent the release must abort and leave the embargo
    bucket files in place so the operator can fix the dataset and retry.
    """
    s3_key = os.path.join(S3_PREFIX_TO_MOVE, FILENAME)
    embargo_bucket.upload_file(Filename=FILENAME, Key=s3_key)

    request_id = str(uuid.uuid4())
    with pytest.raises(FileNotFoundError):
        release_files(request_id, S3_PREFIX_TO_MOVE, EMBARGO_BUCKET, PUBLISH_BUCKET)

    # The embargo file must still be there.
    assert s3_key in s3_keys(embargo_bucket)


def test_release_aborts_when_manifest_references_missing_file(
    publish_bucket, embargo_bucket
):
    """
    If the manifest lists a file that isn't present in the embargo bucket,
    the release must abort and leave embargo untouched so the operator can
    correct the dataset and retry.
    """
    present_key = os.path.join(S3_PREFIX_TO_MOVE, FILENAME)
    embargo_bucket.upload_file(Filename=FILENAME, Key=present_key)

    # Manifest references the uploaded file AND a file that was never uploaded.
    missing_rel_path = "files/never_uploaded.txt"
    manifest_key, _ = upload_manifest(
        embargo_bucket, S3_PREFIX_TO_MOVE, [FILENAME, missing_rel_path]
    )

    request_id = str(uuid.uuid4())
    with pytest.raises(FileNotFoundError):
        release_files(request_id, S3_PREFIX_TO_MOVE, EMBARGO_BUCKET, PUBLISH_BUCKET)

    # Embargo should still contain everything we put in it.
    assert present_key in s3_keys(embargo_bucket)
    assert manifest_key in s3_keys(embargo_bucket)


def upload_manifest(embargo_bucket, prefix, file_paths, *, with_sha256=()):
    """
    Build and upload a minimal manifest.json under `prefix` referencing the
    given relative file paths. `with_sha256` lists the paths that should
    receive a (stale) sha256 field; those entries also get a
    sourcePackageId, mimicking the production manifest format where
    user-uploaded files carry both a sha256 and a sourcePackageId while
    system-generated files carry neither.

    Returns a (key, manifest_dict) tuple so callers can assert that
    pass-through fields on the rewritten manifest still match the original.
    """
    sha256_paths = set(with_sha256)
    files = [
        {
            "name": "manifest.json",
            "path": MANIFEST_RELATIVE_PATH,
            "size": 0,
            "fileType": "Json",
        }
    ]
    for i, rel_path in enumerate(file_paths):
        entry = {
            "name": os.path.basename(rel_path),
            "path": rel_path,
            "size": 15,
            "fileType": "Text",
            "s3VersionId": f"stale-version-{i}",
        }
        if rel_path in sha256_paths:
            entry["sha256"] = f"stale-sha256-{i}"
            entry["sourcePackageId"] = f"N:package:fake-package-{i}"
        files.append(entry)
    manifest = {"pennsieveDatasetId": 1234, "version": 1, "files": files}
    key = os.path.join(prefix, MANIFEST_RELATIVE_PATH)
    embargo_bucket.put_object(
        Key=key, Body=json.dumps(manifest).encode("utf-8")
    )
    return key, manifest


def setup_bucket(bucket_name, versioned):
    s3_resource.create_bucket(Bucket=bucket_name)
    bucket = s3_resource.Bucket(bucket_name)
    if versioned:
        bucket.Versioning().enable()
    bucket.objects.all().delete()
    return bucket


def s3_keys(bucket):
    return [obj.key for obj in bucket.objects.all()]


def create_keys(prefix, filename, n):
    return ["{}/{}{}".format(prefix, i, filename) for i in range(n)]
