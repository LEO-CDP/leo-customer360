def test_models_allow_metadata_column_mapping():
    from model.ad import Ad
    from model.campaign import Campaign
    from model.placement import Placement

    assert Ad.__table__.columns["metadata"].name == "metadata"
    assert Campaign.__table__.columns["metadata"].name == "metadata"
    assert Placement.__table__.columns["metadata"].name == "metadata"
