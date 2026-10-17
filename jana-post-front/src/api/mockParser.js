// oxlint-disable-next-line no-control-regex
const INVISIBLE =/[\u0000-\u001F\u007F\u00A0\u00AD\u200B-\u200F\u2028\u2029\u202F\u2060\uFEFF]/g;

const REPUBLIC_CITIES = ['Алматы', 'Астана', 'Шымкент'];

const DEMO_REFERENCE = {
  'алматы|аксай 3а': 'Ауэзовский',
  'алматы|жетысу': 'Ауэзовский',
  'алматы|самал': 'Медеуский',
  'алматы|абая': 'Бостандыкский',
  'астана|туран': 'Есиль',
  'астана|кабанбай батыра': 'Есиль',
};

const STREET_TYPE_ALIASES = [
  [/^(ул|улица|көшесі)$/iu, 'улица'],
  [/^(пр|пр-т|просп|проспект|даңғылы)$/iu, 'проспект'],
  [/^(мкр|мкрн|мкр-н|микрорайон|микр)$/iu, 'микрорайон'],
  [/^(пер|переулок)$/iu, 'переулок'],
  [/^(кв-л|квартал)$/iu, 'квартал'],
  [/^(б-р|бульв|бульвар)$/iu, 'бульвар'],
  [/^(ш|шоссе)$/iu, 'шоссе'],
];

const TYPE_WORDS = 'улица|ул|проспект|пр-т|просп|пр|микрорайон|мкрн|мкр-н|мкр|переулок|пер|квартал|кв-л|бульвар|б-р|шоссе';
const HOUSE = '\\d+[а-яa-z]?(?:/\\d+[а-яa-z]?)?';
const B = '(?<![\\p{L}])';
const E = '(?![\\p{L}])';
const re = (source) => new RegExp(source, 'iu');

function streetTypeOf(word) {
  return STREET_TYPE_ALIASES.find(([pattern]) => pattern.test(word.replace(/\.$/, '')))?.[1] ?? null;
}

function take(state, pattern, pick = (m) => m[1]) {
  const m = state.text.match(pattern);
  if (!m) return null;
  state.text = `${state.text.slice(0, m.index)} , ${state.text.slice(m.index + m[0].length)}`;
  return pick(m);
}

function leftovers(state) {
  return state.text
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

function parseOne({ id, raw_address }) {
  const state = {
    text: raw_address
      .replace(INVISIBLE, ' ')
      .replace(/(\d)(\p{L}{2,})/gu, '$1 $2')
      .replace(/(\p{L}{3,})(\d)/gu, '$1 $2')
      .replace(/\s+/g, ' ')
      .trim(),
  };
  const out = { id, raw_address };
  let score = 0;

  const city = take(state, /^(?:город|г\.)\s*([^/,]+?)\s*\/[^,]*,?/iu);
  if (city) {
    out.city = city.trim();
    if (REPUBLIC_CITIES.includes(out.city)) out.province = out.city;
    score += 2;
  }
  const province = take(state, re(`(\\p{L}+ская)\\s+(?:область|обл\\.?)${E}`), (m) => `${m[1]} область`);
  if (province) {
    out.province = province;
    score += 1;
  }
  out.district = take(state, re(`(\\p{L}+(?:ский|цкий))\\s+(?:район|р-н)${E}`));

  out.apartment =
    take(state, re(`${B}(?:кв|квартира|flat|kv)\\.?\\s*(\\d+[а-яa-z]?)${E}`)) ??
    take(state, re(`${B}(\\d+)\\s*(?:кв|квартира)${E}\\.?`));
  out.entrance = take(state, re(`(\\d+)\\s*под[ъь]?езд${E}`)) ?? take(state, re(`${B}под[ъь]?езд\\s*(\\d+)`));
  out.floor = take(state, re(`(\\d+)\\s*этаж${E}`)) ?? take(state, re(`${B}этаж\\s*(\\d+)`));
  score += [out.apartment, out.entrance, out.floor].filter(Boolean).length * 0.5;

  out.house = take(state, re(`${B}(?:дом|д\\.)\\s*(${HOUSE})`)) ?? take(state, re(`${B}(${HOUSE})\\s*дом${E}`));
  out.landmark = take(state, re(`${B}((?:магазин|маг\\.|тц|трц|кафе|школа|qazpost)\\s+[^,\\d]+)`), (m) => m[1].trim());

  const typed =
    take(state, re(`${B}(${TYPE_WORDS})${E}[\\s.]*([^,\\s][^,]*)`), (m) => [m[1], m[2]]) ??
    take(state, re(`((?:\\p{L}+\\s+)?\\d*|\\p{L}+)\\s+(${TYPE_WORDS})${E}\\.?`), (m) => [m[2], m[1]]);
  if (typed) {
    out.street_type = streetTypeOf(typed[0]);
    out.street_name = typed[1].trim() || null;
  }

  const rest = leftovers(state);
  if (!out.street_name) {
    const named = rest.find((s) => /\p{L}{3,}/u.test(s));
    if (named) {
      out.street_name = named;
      rest.splice(rest.indexOf(named), 1);
    }
  }
  const tail = out.street_name?.match(re(`^(.*\\p{L}.*?)\\s+(${HOUSE})$`));
  if (tail && !out.house && out.street_type !== 'микрорайон') {
    out.street_name = tail[1];
    out.house = tail[2];
  }
  if (!out.house) out.house = rest.find((s) => re(`^${HOUSE}$`).test(s)) ?? null;

  if (out.street_name) score += 1;
  if (out.house) score += 1;

  const refKey = `${(out.city || '').toLowerCase()}|${(out.street_name || '').toLowerCase()}`;
  if (!out.district && DEMO_REFERENCE[refKey]) out.district = DEMO_REFERENCE[refKey];

  out.confidence = Math.round(Math.min(0.97, 0.2 + score * 0.13) * 100) / 100;
  return out;
}

export async function mockParseBatch(records) {
  await new Promise((resolve) => setTimeout(resolve, 150 + records.length * 2));
  return records.map(parseOne);
}
