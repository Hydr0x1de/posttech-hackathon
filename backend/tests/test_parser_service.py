"""Тесты сервиса разбора: fallback без БД, слайсы исходной строки, стандартизация."""

from __future__ import annotations

import pytest

from app.repositories.repository import (
    Classifier,
    ClassifierIndex,
    NullAddressRepository,
    RepositoryManager,
)
from app.services.parser import AddressParserService

# --------------------------------------------------------------------------- #
# Без базы: базовый regex-разбор, confidence ровно 0.5
# --------------------------------------------------------------------------- #


@pytest.fixture()
def null_parser() -> AddressParserService:
    return AddressParserService(lambda: NullAddressRepository())


@pytest.fixture()
def db_parser(tmp_path, demo_db) -> AddressParserService:
    manager = RepositoryManager(str(demo_db), reload_interval=0)
    return AddressParserService(manager.get)


def test_raw_address_returned_unchanged(null_parser):
    raw = "  г. Актобе, ул. АБАЯ,  д.55/6, кв 12  "
    result = null_parser.parse("rec_1", raw)
    assert result.raw_address == raw


def test_confidence_without_db_is_half(null_parser):
    result = null_parser.parse("rec_1", "Алматы, ул. Абая 12, кв 4")
    assert result.confidence == 0.5


def test_no_typo_correction(null_parser):
    # Опечатка и регистр должны остаться как в исходной строке
    raw = "УЛ. КАЗИБЕК БИ 12"
    result = null_parser.parse("rec_1", raw)
    assert result.street_name == "КАЗИБЕК БИ"
    assert result.raw_address == raw


def test_empty_address(null_parser):
    result = null_parser.parse("rec_1", "")
    assert result.confidence == 0.0
    assert result.street_name is None


def test_house_and_apartment(null_parser):
    result = null_parser.parse("rec_1", "Абая 55/6, кв 12")
    assert (result.house, result.apartment) == ("55/6", "12")


def test_glued_writing(null_parser):
    result = null_parser.parse("rec_1", "66квартал 4подьезд 12дом flat191")
    assert result.house == "12"
    assert result.entrance == "4"
    assert result.apartment == "191"
    assert result.street_type == "квартал"
    assert result.living_area == "66квартал"


def test_living_area_from_type(null_parser):
    result = null_parser.parse("rec_1", "12 микрорайон, дом 5")
    assert result.living_area == "12 микрорайон"
    assert result.street_type == "микрорайон"


def test_positional_fallback(null_parser):
    result = null_parser.parse("rec_1", "Абая 55/6 4 9")
    assert (result.house, result.entrance, result.floor) == ("55/6", "4", "9")


# --------------------------------------------------------------------------- #
# С базой
# --------------------------------------------------------------------------- #


def test_db_classifiers_and_street(db_parser):
    result = db_parser.parse("rec_1", "Алматы, Ауэзовский район, Казыбек би 12а кв. 4")
    assert result.province == "Алматы"
    assert result.city == "Алматы"
    assert result.district == "Ауэзовский"          # без слова "район"
    assert result.street_name == "Казыбек би"       # слайс исходной строки
    assert result.street_type == "улица"            # базовое слово
    assert result.house == "12а"
    assert result.apartment == "4"
    assert result.confidence > 0.9


def test_db_street_type_from_database(db_parser):
    result = db_parser.parse("rec_1", "Алматы, Абая 55")
    assert result.street_type == "проспект"  # в БД Абая — проспект, в тексте типа нет


def test_db_enriches_parents(db_parser):
    result = db_parser.parse("rec_1", "Мангилик Ел 55/1")
    assert result.city == "Астана"
    assert result.province == "Астана"
    assert result.district == "Есильский"


def test_db_alias_lookup_keeps_user_spelling(db_parser):
    result = db_parser.parse("rec_1", "Алматы, ул. Фурманова 55")
    assert result.street_name == "Фурманова"  # не заменяем на официальное название
    assert result.street_type == "улица"


def test_db_living_area(db_parser):
    result = db_parser.parse("rec_1", "Актобе, 12 микрорайон, дом 5")
    assert result.living_area == "12 микрорайон"
    assert result.street_name is None
    assert result.city == "Актобе"


def test_bilingual_glue(db_parser):
    raw = "город Актобе/Ақтөбе қаласы, 12 микрорайон, дом 5, кв 3"
    result = db_parser.parse("rec_1", raw)
    assert result.city == "Актобе"
    assert result.living_area == "12 микрорайон"
    assert result.raw_address == raw


def test_province_from_text_is_standardized(db_parser):
    result = db_parser.parse("rec_1", "Актюбинская область, город Актобе, Абилкайыр хана 12/1")
    assert result.province == "Актюбинская область"
    assert result.city == "Актобе"
    assert result.street_name == "Абилкайыр хана"


def test_unknown_street_keeps_text(db_parser):
    result = db_parser.parse("rec_1", "улица Казахстанская 15")
    assert result.street_name == "Казахстанская"
    assert result.street_type == "улица"
    assert result.house == "15"


def test_db_keeps_user_spelling(db_parser):
    # В БД "Казыбек би", пользователь написал с опечаткой — слайс не исправляем
    result = db_parser.parse("rec_1", "Алматы, ул. казибек би 12")
    assert result.street_name == "казибек би"
    assert result.street_type == "улица"  # тип подтянут регуляркой
    assert result.city == "Алматы"


def test_index_is_empty_for_null_repository():
    repo = NullAddressRepository()
    index = repo.classifiers()
    assert isinstance(index, ClassifierIndex)
    assert index.is_empty
    assert repo.search_streets(["абая"]) == []
    assert repo.stats()["db_loaded"] is False


def test_classifier_index_helper():
    item = Classifier(id=1, name="Ауэзовский", norm="ауэзовский")
    index = ClassifierIndex(districts=[item], district_keys={"ауэзовск": [item]})
    assert index.is_empty is False
    assert index.district_keys["ауэзовск"][0].name == "Ауэзовский"


def test_cache_returns_same_result(db_parser):
    first = db_parser.parse("a", "Алматы, Абая 55")
    second = db_parser.parse("b", "Алматы, Абая 55")
    assert first.to_dict() | {"id": "b"} == second.to_dict()


def test_batch_preserves_order(null_parser):
    records = [(f"rec_{i}", f"Абая {i}") for i in range(200)]
    results = null_parser.parse_batch(records)
    assert [r.id for r in results] == [rid for rid, _ in records]
    assert [r.house for r in results] == [str(i) for i in range(200)]


def test_batch_empty(null_parser):
    assert null_parser.parse_batch([]) == []


# --------------------------------------------------------------------------- #
# Регрессии: казахские названия, длина слайса, порог похожести,
# порядок подъезд/этаж, авто-обогащение province по городу
# --------------------------------------------------------------------------- #


@pytest.fixture()
def truncated_parser(truncated_db) -> AddressParserService:
    return AddressParserService(RepositoryManager(str(truncated_db), reload_interval=0).get)


def test_kazakh_name_is_not_trimmed(db_parser):
    """Казахское написание названия не должно резаться до последнего слова."""
    result = db_parser.parse("rec_1", "Алматы, Қабанбай батыр 12а")
    assert result.street_name == "Қабанбай батыр"
    assert result.house == "12а"


def test_kazakh_name_kept_in_fallback(db_parser):
    """Нет совпадения в БД — в слайсе остаётся всё название, а не «Ел»."""
    result = db_parser.parse("rec_1", "Астана, Мәңгілік Ел 55/1")
    assert result.street_name == "Мәңгілік Ел"
    assert result.city == "Астана"


def test_slice_uses_full_official_name(truncated_parser):
    """Куцый name_norm ("12") не должен обрезать слайс до "12"."""
    result = truncated_parser.parse("rec_1", "12 микрорайон, дом 5")
    assert result.living_area == "12 микрорайон"
    assert result.street_name is None
    assert result.city == "Караганда"


def test_weak_similarity_goes_to_regex_fallback(truncated_parser):
    """Похожесть ниже 0.8: ни обогащения, ни confidence > 0.5."""
    result = truncated_parser.parse("rec_1", "66квартал 4подьезд 12дом")
    assert result.province is None
    assert result.city is None
    assert result.district is None
    assert result.living_area == "66квартал"
    assert result.confidence == 0.5


@pytest.mark.parametrize(
    "raw",
    ["Абая 12, 2 подъезд 5 этаж", "Абая 12, 5 этаж 2 подъезд", "Абая 12, подъезд 2, этаж 5"],
)
def test_entrance_floor_order(null_parser, raw):
    result = null_parser.parse("rec_1", raw)
    assert (result.entrance, result.floor) == ("2", "5")


def test_province_filled_from_city_dictionary(db_parser):
    """Город известен, улицы в БД нет — провинция всё равно подтягивается."""
    result = db_parser.parse("rec_1", "Караганда, неизвестный проезд 12")
    assert result.city == "Караганда"
    assert result.province == "Карагандинская область"


def test_province_from_city_only_when_missing(db_parser):
    result = db_parser.parse("rec_1", "Актюбинская область, Актобе, Абилкайыр хана 12")
    assert result.province == "Актюбинская область"
    assert result.city == "Актобе"


def test_classifier_stem_does_not_create_false_province(db_parser):
    """"Абая" не должна превращаться в провинцию "Абай область"."""
    result = db_parser.parse("rec_1", "Абая 12, 2 подъезд 5 этаж")
    assert result.province != "Абай область"


def test_exact_match_prefers_entity_with_street_type():
    """Дубли без street_type не должны «съедать» тип улицы из БД."""
    from app.core.text import Token
    from app.repositories.repository import StreetEntity
    from app.services.parser import _exact_match

    tokens = [Token("Казыбек", "казыбек", 0, 7), Token("би", "би", 8, 10)]

    def entity(entity_id: int, street_type: str | None) -> StreetEntity:
        return StreetEntity(
            id=entity_id,
            entity_type="street",
            name_official="Казыбек би",
            name_norm="казыбек би",
            name_core_norm="казыбек би",
            street_type=street_type,
            city_id=1,
            district_id=None,
            province_id=1,
            names=("казыбек би",),
        )

    match = _exact_match(tokens, [entity(1, None), entity(2, "улица")])
    assert match is not None
    assert match.entity.street_type == "улица"


def test_unlinked_leading_entity_blocks_city_hallucination():
    """"Нурсити" не слинкован с городом — чужой city из "2 микрорайон" не берём."""
    from app.core.text import Token
    from app.repositories.repository import StreetEntity
    from app.services.parser import _EntityMatch, _has_unlinked_leading_candidate

    tokens = [
        Token("Нурсити", "нурсити", 0, 7),
        Token("2", "2", 8, 9),
        Token("микрорайон", "микрорайон", 10, 20),
    ]

    def entity(entity_id: int, name: str, core: str, city_id: int | None) -> StreetEntity:
        return StreetEntity(
            id=entity_id,
            entity_type="street" if city_id is None else "living_area",
            name_official=name,
            name_norm=core,
            name_core_norm=core,
            street_type="улица" if city_id is None else "микрорайон",
            city_id=city_id,
            district_id=367,
            province_id=3,
            names=(core,),
        )

    unlinked = entity(1, "Нурсити", "нурсити", None)
    linked = entity(2, "2 микрорайон", "2 микрорайон", 255)
    best = _EntityMatch(linked, start=8, end=20, score=1.0)
    assert _has_unlinked_leading_candidate(tokens, [unlinked, linked], best) is True
    assert _has_unlinked_leading_candidate(tokens, [linked], best) is False


def test_district_name_yields_to_street_without_explicit_hint():
    """"Туран" без слова "район" — сначала улица/проспект, а не район."""
    from app.core.text import Token
    from app.repositories.repository import Classifier, StreetEntity
    from app.services.parser import _Classification, _district_shadowed_by_street

    class Repo:
        def search_streets(self, tokens, **_kwargs):
            return [
                StreetEntity(
                    id=1,
                    entity_type="street",
                    name_official="проспект Туран",
                    name_norm="проспект туран",
                    name_core_norm="туран",
                    street_type="проспект",
                    city_id=22,
                    district_id=None,
                    province_id=6,
                    names=("проспект туран", "туран"),
                )
            ]

    item = _Classification(
        kind="district",
        item=Classifier(id=483, name="Туран", norm="туран", parent_id=23),
        start=0,
        end=1,
    )
    tokens = [Token("Туран", "туран", 0, 5)]
    assert _district_shadowed_by_street(Repo(), tokens, item) is True

    tokens_with_hint = [Token("район", "район", 0, 5), Token("Туран", "туран", 6, 11)]
    item_with_hint = _Classification(
        kind="district", item=item.item, start=0, end=2
    )
    assert _district_shadowed_by_street(Repo(), tokens_with_hint, item_with_hint) is False