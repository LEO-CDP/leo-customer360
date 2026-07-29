"""Unit tests for core.utils.dagster_client -- the OOP wrapper around
dagster_graphql.DagsterGraphQLClient used to trigger backend-system Dagster
jobs. All tests inject a fake client_factory (no real DagsterGraphQLClient/
HTTP connection), so they run without a Dagster webserver.
"""

import unittest
from unittest.mock import MagicMock

from dagster_graphql import DagsterGraphQLClientError

from core.utils.dagster_client import (
    AnalyticsDagsterService,
    DagsterJobTriggerError,
    DagsterService,
    DataSynchDagsterService,
    EmailEngineDagsterService,
    IdentityResolutionDagsterService,
    NotificationEngineDagsterService,
    ScoringDagsterService,
    SegmentationDagsterService,
    dagster_client,
)


class DagsterServiceSubmitTests(unittest.TestCase):
    def _service(self, fake_client) -> DagsterService:
        return DagsterService(
            job_name="some_job",
            location_name="some_location",
            repository_name="__repository__",
            client_factory=lambda: fake_client,
        )

    def test_submit_returns_run_id_on_success(self):
        fake_client = MagicMock()
        fake_client.submit_job_execution.return_value = "run-abc"
        service = self._service(fake_client)

        run_id = service.submit(run_config={"ops": {}}, tags={"reason": "test"})

        self.assertEqual(run_id, "run-abc")
        fake_client.submit_job_execution.assert_called_once_with(
            "some_job",
            repository_location_name="some_location",
            repository_name="__repository__",
            run_config={"ops": {}},
            tags={"reason": "test"},
            op_selection=None,
        )

    def test_submit_wraps_dagster_graphql_client_error(self):
        fake_client = MagicMock()
        fake_client.submit_job_execution.side_effect = DagsterGraphQLClientError("bad config", "trace")
        service = self._service(fake_client)

        with self.assertRaises(DagsterJobTriggerError):
            service.submit()

    def test_submit_wraps_connection_errors(self):
        fake_client = MagicMock()
        fake_client.submit_job_execution.side_effect = ConnectionRefusedError("refused")
        service = self._service(fake_client)

        with self.assertRaises(DagsterJobTriggerError):
            service.submit()

    def test_get_status_maps_success(self):
        fake_client = MagicMock()
        fake_client.get_run_status.return_value.value = "SUCCESS"
        service = self._service(fake_client)

        result = service.get_status("run-abc")

        self.assertEqual(result, {"run_id": "run-abc", "raw_status": "SUCCESS", "status": "success"})

    def test_get_status_maps_failure(self):
        fake_client = MagicMock()
        fake_client.get_run_status.return_value.value = "FAILURE"
        service = self._service(fake_client)

        result = service.get_status("run-abc")

        self.assertEqual(result["status"], "failure")

    def test_get_status_maps_running(self):
        fake_client = MagicMock()
        fake_client.get_run_status.return_value.value = "STARTED"
        service = self._service(fake_client)

        result = service.get_status("run-abc")

        self.assertEqual(result["status"], "running")

    def test_get_status_wraps_errors(self):
        fake_client = MagicMock()
        fake_client.get_run_status.side_effect = RuntimeError("boom")
        service = self._service(fake_client)

        with self.assertRaises(DagsterJobTriggerError):
            service.get_status("run-abc")


class SegmentationDagsterServiceTests(unittest.TestCase):
    def _service_with_fake_client(self, fake_client):
        service = SegmentationDagsterService()
        service._client_factory = lambda: fake_client
        return service

    def test_refresh_requires_tenant_id(self):
        service = self._service_with_fake_client(MagicMock())

        with self.assertRaises(ValueError):
            service.refresh(tenant_id="")

    def test_refresh_scopes_run_config_and_tags_to_tenant(self):
        fake_client = MagicMock()
        fake_client.submit_job_execution.return_value = "run-1"
        service = self._service_with_fake_client(fake_client)

        run_id = service.refresh(tenant_id="tenant-1")

        self.assertEqual(run_id, "run-1")
        _, kwargs = fake_client.submit_job_execution.call_args
        self.assertEqual(
            kwargs["run_config"],
            {"ops": {"recompute_segments_op": {"config": {"tenant_id": "tenant-1"}}}},
        )
        self.assertEqual(kwargs["tags"], {"trigger_reason": "refresh"})

    def test_create_and_update_tag_runs_distinctly(self):
        fake_client = MagicMock()
        fake_client.submit_job_execution.return_value = "run-1"
        service = self._service_with_fake_client(fake_client)

        service.create(tenant_id="tenant-1")
        service.update(tenant_id="tenant-1")

        reasons = [call.kwargs["tags"]["trigger_reason"] for call in fake_client.submit_job_execution.call_args_list]
        self.assertEqual(reasons, ["create", "update"])


class DagsterClientFacadeTests(unittest.TestCase):
    def test_facade_exposes_one_service_per_backend_system_location(self):
        self.assertIsInstance(dagster_client.analytics, AnalyticsDagsterService)
        self.assertIsInstance(dagster_client.identity_resolution, IdentityResolutionDagsterService)
        self.assertIsInstance(dagster_client.scoring, ScoringDagsterService)
        self.assertIsInstance(dagster_client.segmentation, SegmentationDagsterService)
        self.assertIsInstance(dagster_client.data_synch, DataSynchDagsterService)
        self.assertIsInstance(dagster_client.email_engine, EmailEngineDagsterService)
        self.assertIsInstance(dagster_client.notification_engine, NotificationEngineDagsterService)

    def test_service_job_and_location_names_match_backend_system_dagster_defs(self):
        self.assertEqual(dagster_client.analytics.job_name, "analytics_job")
        self.assertEqual(dagster_client.analytics.location_name, "analytics")
        self.assertEqual(dagster_client.identity_resolution.job_name, "identity_resolution_job")
        self.assertEqual(dagster_client.identity_resolution.location_name, "identity_resolution")
        self.assertEqual(dagster_client.scoring.job_name, "scoring_job")
        self.assertEqual(dagster_client.scoring.location_name, "scoring")
        self.assertEqual(dagster_client.segmentation.job_name, "segmentation_job")
        self.assertEqual(dagster_client.segmentation.location_name, "segmentation")
        self.assertEqual(dagster_client.data_synch.job_name, "data_synch_job")
        self.assertEqual(dagster_client.data_synch.location_name, "data_synch")
        self.assertEqual(dagster_client.email_engine.job_name, "email_engine_job")
        self.assertEqual(dagster_client.email_engine.location_name, "email_engine")
        self.assertEqual(dagster_client.notification_engine.job_name, "notification_engine_job")
        self.assertEqual(dagster_client.notification_engine.location_name, "notification_engine")


if __name__ == "__main__":
    unittest.main()
