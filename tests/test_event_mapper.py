"""Event mapper — codes confirmed on DS-K1T342MWX (see docs/DISCOVERY)."""

from hikvision_access import event_mapper as em
from hikvision_access.const import (
    METHOD_CARD,
    METHOD_FACE,
    RESULT_DENIED,
    RESULT_GRANTED,
)


def test_face_verified_is_granted():
    m = em.map_event(5, 75)
    assert m.access_result == RESULT_GRANTED
    assert m.is_access_decision
    assert m.entity_event == "access_granted"


def test_face_failed_is_denied():
    assert em.map_event(5, 76).access_result == RESULT_DENIED


def test_door_lifecycle_is_not_an_access_decision():
    for minor in (21, 22, 23, 24):
        assert not em.map_event(5, minor).is_access_decision


def test_door_contact_maps_to_entity_events():
    assert em.map_event(5, 23).entity_event == "door_opened"
    assert em.map_event(5, 24).entity_event == "door_closed"


def test_unknown_code_is_flagged_not_dropped():
    m = em.map_event(5, 9999)
    assert m is em.UNKNOWN_MAPPING
    assert not em.is_known(5, 9999)


def test_string_codes_are_accepted():
    assert em.map_event("5", "75").access_result == RESULT_GRANTED


def test_none_codes_do_not_crash():
    assert em.map_event(None, None) is em.UNKNOWN_MAPPING


def test_refine_method_prefers_verify_mode():
    assert em.refine_method(em.map_event(5, 75), "cardOrFace") == METHOD_FACE
    assert em.refine_method(em.map_event(5, 1), "card") == METHOD_CARD
    # falls back to the code's own method when verifyMode is unhelpful
    assert em.refine_method(em.map_event(5, 75), "invalid") == METHOD_FACE
