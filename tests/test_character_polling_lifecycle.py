"""Day 2: deterministic worker state/timeout checks, no external provider."""

from unittest.mock import Mock, patch

import httpx
import pytest

from apps.characters.models import CharacterGenerationJob, ImgGenLog
from apps.characters.tasks import (
    _poll_character_result,
    process_character_generation_job,
)


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["IN_PROGRESS", "SUCCEEDED", "CONSUMED", "FAILED"])
def test_duplicate_delivery_does_not_restart_job(user, status, settings):
    settings.AI_SERVICE_URL = "http://ai.test"
    settings.AI_SERVICE_TOKEN = "day2-test-key"  # noqa: S105
    job = CharacterGenerationJob.objects.create(user=user, status=status)
    with patch(
        "apps.characters.tasks._submit_character_job",
        side_effect=RuntimeError("unexpected duplicate"),
    ) as submit:
        process_character_generation_job(str(job.pk), "몽이", "친구")
    submit.assert_not_called()
    job.refresh_from_db()
    assert job.status == status


@pytest.mark.django_db
@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("deadline"),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("offline"),
    ],
)
def test_poll_exception_marks_failed_and_refunds_once(user, settings, failure):
    settings.AI_SERVICE_URL = "http://ai.test"
    job = CharacterGenerationJob.objects.create(user=user)
    log = ImgGenLog.objects.create(user=user, gen_cnt=1)
    with (
        patch("apps.characters.tasks._submit_character_job", return_value="ai-1"),
        patch("apps.characters.tasks._poll_character_result", side_effect=failure),
    ):
        process_character_generation_job(str(job.pk), "몽이", "친구", log.pk)
    job.refresh_from_db()
    assert job.status == "FAILED"
    assert not ImgGenLog.objects.filter(pk=log.pk).exists()


def test_worker_pending_times_out_without_automatic_retry():
    response = Mock()
    response.json.return_value = {"status": "pending", "result": None, "error": None}
    with (
        patch("apps.characters.tasks.httpx.get", return_value=response) as get,
        patch("apps.characters.tasks.time.monotonic", side_effect=[0, 0, 300]),
        patch("apps.characters.tasks.time.sleep") as sleep,
        pytest.raises(TimeoutError),
    ):
        _poll_character_result(base_url="http://ai.test", ai_job_id="ai-1", headers={})
    assert get.call_count == 2
    sleep.assert_called_once_with(3.0)
    assert get.call_args.kwargs["timeout"] == 30.0
