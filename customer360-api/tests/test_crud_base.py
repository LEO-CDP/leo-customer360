import unittest
import uuid
from unittest.mock import MagicMock

from core.crud.base import CRUDBase
from core.models.identity import CdpDomainProfile, CdpScoringModel


class TestCRUDBase(unittest.TestCase):
    def test_list_applies_desc_sort_to_mapped_column(self):
        mock_db = MagicMock()
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        crud = CRUDBase(CdpScoringModel)
        crud.list(mock_db, sort_by="updated_at DESC")

        executed_stmt = mock_db.execute.call_args.args[0]
        rendered_sql = str(executed_stmt)
        self.assertIn("ORDER BY", rendered_sql)
        self.assertIn("cdp_scoring_models.updated_at DESC", rendered_sql)

    def test_list_rejects_invalid_sort_direction(self):
        mock_db = MagicMock()
        crud = CRUDBase(CdpScoringModel)

        with self.assertRaises(ValueError):
            crud.list(mock_db, sort_by="updated_at DESC; DROP TABLE cdp_scoring_models")

        mock_db.execute.assert_not_called()

    def test_update_sets_timezone_aware_updated_at_from_db_clock(self):
        mock_db = MagicMock()
        crud = CRUDBase(CdpScoringModel)
        model = CdpScoringModel(
            scoring_model_name="churn_prediction_v2",
            display_name="XGBoost Churn Predictor",
            model_type="classification",
            status="ACTIVE",
        )

        crud.update(mock_db, model, {"display_name": "Updated Model"})

        self.assertEqual(model.display_name, "Updated Model")
        self.assertEqual(str(model.updated_at), "now()")

    def test_update_sets_naive_updated_at_in_utc_from_db_clock(self):
        mock_db = MagicMock()
        crud = CRUDBase(CdpDomainProfile)
        model = CdpDomainProfile(
            tenant_id=uuid.uuid4(),
            master_profile_id=uuid.uuid4(),
            domain_id=uuid.uuid4(),
        )

        crud.update(mock_db, model, {"profile_name": "VIP Shopper"})

        self.assertEqual(model.profile_name, "VIP Shopper")
        self.assertIn("timezone(:timezone_1, now())", str(model.updated_at))


if __name__ == "__main__":
    unittest.main()