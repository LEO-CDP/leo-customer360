import pytest
from pydantic import ValidationError

from leo_customer360_dao.schemas.identity import MasterProfileCreate, MasterProfileUpdate


@pytest.mark.parametrize(
    "field",
    [
        "email_opt_in",
        "sms_opt_in",
        "push_opt_in",
        "zalo_opt_in",
        "whatsapp_opt_in",
        "voice_opt_in",
        "meta_ads_opt_in",
        "google_ads_opt_in",
        "tiktok_ads_opt_in",
        "leo_ads_opt_in",
    ],
)
def test_profile_consent_flags_require_json_booleans(field):
    payload = {"communication_preferences": {field: True}}
    assert MasterProfileUpdate.model_validate(payload).communication_preferences == {
        field: True
    }

    with pytest.raises(ValidationError, match="must be a JSON boolean"):
        MasterProfileUpdate.model_validate(
            {"communication_preferences": {field: "true"}}
        )


def test_profile_apis_cannot_write_derived_eligibility_policy():
    for schema in (MasterProfileCreate, MasterProfileUpdate):
        with pytest.raises(ValidationError, match="derived result"):
            schema.model_validate(
                {"communication_preferences": {"eligibility_policy": {"decision": "allow"}}}
            )


@pytest.mark.parametrize(
    "field, value",
    [
        ("predictive_clv", "1000.00"),
        ("clv_segment", "champion"),
        ("clv_percentile", "95.0"),
        ("current_valuation_id", "5620d1ff-2fe3-4351-95a4-990174285854"),
        ("predictive_clv", None),
    ],
)
def test_profile_patch_cannot_write_projected_clv_fields(field, value):
    with pytest.raises(ValidationError, match="derived from cdp_customer_valuation"):
        MasterProfileUpdate.model_validate({field: value})
