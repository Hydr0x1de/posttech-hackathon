export const FIELDS = [
  { key: 'province', short: 'обл.', group: 'place', label: 'Область', hint: '«Актюбинская область», «Алматы» для городов респ. значения' },
  { key: 'city', short: 'г.', group: 'place', label: 'Город', hint: 'Только название, без «город»/«пос.» и казахской половины' },
  { key: 'district', short: 'р-н', group: 'place', label: 'Район', hint: 'Без слова «район»: «Ауэзовский»', reference: true },
  { key: 'living_area', short: 'ЖК', group: 'place', label: 'Мкр / ЖК', hint: 'Микрорайон или ЖК, если улица указана отдельно', reference: true },
  { key: 'street_type', short: 'тип', group: 'street', label: 'Тип улицы', hint: 'улица, проспект, микрорайон, переулок, квартал…' },
  { key: 'street_name', short: 'ул.', group: 'street', label: 'Улица', hint: 'Без типа, ровно как в строке — падеж и опечатки не трогаем' },
  { key: 'house', short: 'д.', group: 'street', label: 'Дом', hint: 'С дробью и литерой: «1/2», «12а»' },
  { key: 'apartment', short: 'кв.', group: 'courier', label: 'Квартира', hint: 'Только номер' },
  { key: 'entrance', short: 'под.', group: 'courier', label: 'Подъезд', hint: 'Только номер' },
  { key: 'floor', short: 'эт.', group: 'courier', label: 'Этаж', hint: 'Только номер' },
  { key: 'landmark', short: 'ориентир', group: 'courier', label: 'Ориентир', hint: '«магазин Береке»' },
];

export const FIELD_KEYS = FIELDS.map((f) => f.key);

export const OUTPUT_KEYS = ['id', 'raw_address', ...FIELD_KEYS, 'confidence'];

export const STREET_TYPES = [
  'улица',
  'проспект',
  'микрорайон',
  'переулок',
  'квартал',
  'бульвар',
  'шоссе',
  'проезд',
  'тупик',
  'площадь',
  'трасса',
  'жилой массив',
];

export const DEFAULT_THRESHOLD = 0.7;
