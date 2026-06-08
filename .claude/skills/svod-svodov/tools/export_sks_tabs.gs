/**
 * ГК СКС — выгрузка вкладок книг в маленькие CSV (Путь B).
 *
 * Зачем: книга «СводСводов2021» >10 МБ, Drive-коннектор отдаёт только ПЕРВУЮ
 * вкладку. Этот скрипт нарезает нужные вкладки в отдельные маленькие CSV в
 * папку Drive, которые Claude читает по одному — без лимита 10 МБ.
 *
 * УСТАНОВКА (один раз):
 *   1. Открой книгу «СводСводов2021» → Расширения → Apps Script.
 *   2. Вставь этот код, сохрани (Ctrl+S).
 *   3. Перезагрузи книгу → появится меню «📤 Выгрузка СКС».
 *   4. Меню → «Выгрузить всё» (или запусти exportAll() из редактора).
 *
 * Результат в папке Drive «СКС_Данные»:
 *   - СКС_TABS_<книга>.txt        — список всех вкладок (чтобы свериться по именам)
 *   - СКС_<книга>__<вкладка>_<ГГГГ_ММ>.csv
 */

const SKS = {
  folderName: 'СКС_Данные',

  // Книги-источники: ключ → ID
  books: {
    Свод: '1lAvQQlw4w0VO2CVySozxOXJ8h73SWbhIGLy1u2mGK1o', // СводСводов2021
    МП:   '1IIiJhn8_4yZm4bl5v62eUpTq_aa2IQKMGR6qaHRQ4Yg', // Оплата МП
  },

  // Какие вкладки выгружать. '*' = все непустые (кроме skip).
  // Можно сузить до списка точных имён, напр.: ['Лмб','Авто','Скупка','КМЗолото','КМТехника']
  tabs: '*',

  // Пропускать тяжёлый исторический архив (для годового топ-10 потом выгрузим отдельно)
  skipRegex: /^(Истордан|Исторда)/i,

  // Период для имени файла. null = предыдущий месяц от сегодня.
  forceYear: null,   // напр. 2026
  forceMonth: null,  // напр. 5
};

function exportAll() {
  const now = new Date();
  const y = SKS.forceYear  || (SKS.forceMonth ? now.getFullYear()
            : new Date(now.getFullYear(), now.getMonth() - 1, 1).getFullYear());
  const m = SKS.forceMonth || (new Date(now.getFullYear(), now.getMonth() - 1, 1).getMonth() + 1);
  const tag = `${y}_${String(m).padStart(2, '0')}`;
  const folder = getFolder_(SKS.folderName);

  let total = 0;
  for (const [key, id] of Object.entries(SKS.books)) {
    const ss = SpreadsheetApp.openById(id);
    const names = ss.getSheets().map(s => s.getName());
    putFile_(folder, `СКС_TABS_${key}.txt`, names.join('\n'), MimeType.PLAIN_TEXT);

    for (const sheet of ss.getSheets()) {
      const name = sheet.getName();
      if (SKS.skipRegex.test(name)) continue;
      if (SKS.tabs !== '*' && SKS.tabs.indexOf(name) === -1) continue;
      if (sheet.getLastRow() === 0) continue;

      const csv = sheetToCsv_(sheet);
      const safe = name.replace(/[\/\\:*?"<>|]/g, '_');
      putFile_(folder, `СКС_${key}__${safe}_${tag}.csv`, csv, MimeType.PLAIN_TEXT);
      total++;
    }
  }
  const msg = `Выгружено ${total} вкладок за ${tag} в папку «${SKS.folderName}».`;
  Logger.log(msg);
  try { SpreadsheetApp.getUi().alert(msg); } catch (e) {}
}

function sheetToCsv_(sheet) {
  // getValues() = реальные числа (без локали-запятой), удобно для парсинга.
  const data = sheet.getDataRange().getValues();
  return data.map(row => row.map(cellToCsv_).join(',')).join('\n');
}

function cellToCsv_(cell) {
  if (cell instanceof Date) return Utilities.formatDate(cell, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  const s = String(cell === null || cell === undefined ? '' : cell);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function getFolder_(name) {
  const it = DriveApp.getFoldersByName(name);
  return it.hasNext() ? it.next() : DriveApp.createFolder(name);
}

function putFile_(folder, fileName, content, mime) {
  const ex = folder.getFilesByName(fileName);
  while (ex.hasNext()) ex.next().setTrashed(true);
  folder.createFile(fileName, content, mime);
}

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('📤 Выгрузка СКС')
    .addItem('Выгрузить всё', 'exportAll')
    .addToUi();
}
