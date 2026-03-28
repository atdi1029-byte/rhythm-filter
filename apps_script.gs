/**
 * RhythmFilter Apps Script Backend
 *
 * Sheet tabs:
 *   BotState   — A1=JSON blob, B1=timestamp
 *   TradeLog   — append-only trade history (row per trade)
 *   CoinStats  — per-coin running stats
 *
 * All endpoints use GET + JSONP for mobile safety.
 */

function doGet(e) {
  var action = (e.parameter.action || '').toLowerCase();
  var callback = e.parameter.callback || '';
  var result = { status: 'error', message: 'Unknown action' };

  try {
    if (action === 'get_state') {
      result = getState_();
    } else if (action === 'save_state') {
      result = saveState_(e.parameter.data);
    } else if (action === 'log_trade') {
      result = logTrade_(e.parameter.data);
    } else if (action === 'get_trades') {
      result = getTrades_(e.parameter);
    } else if (action === 'get_coins') {
      result = getCoinStats_();
    } else if (action === 'update_coin') {
      result = updateCoinStatus_(e.parameter);
    } else if (action === 'init_coins') {
      result = initCoinStats_(e.parameter.data);
    }
  } catch (err) {
    result = { status: 'error', message: err.toString() };
  }

  var json = JSON.stringify(result);
  if (callback) {
    return ContentService.createTextOutput(callback + '(' + json + ')')
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return ContentService.createTextOutput(json)
    .setMimeType(ContentService.MimeType.JSON);
}


// === BOT STATE ===

function getState_() {
  var sheet = getOrCreateSheet_('BotState');
  var data = sheet.getRange('A1').getValue();
  var ts = sheet.getRange('B1').getValue();
  return {
    status: 'success',
    data: data ? JSON.parse(data) : null,
    timestamp: ts ? new Date(ts).toISOString() : null
  };
}

function saveState_(jsonStr) {
  if (!jsonStr) return { status: 'error', message: 'No data' };
  var sheet = getOrCreateSheet_('BotState');
  sheet.getRange('A1').setValue(jsonStr);
  sheet.getRange('B1').setValue(new Date());
  return { status: 'success' };
}


// === TRADE LOG ===

function logTrade_(jsonStr) {
  if (!jsonStr) return { status: 'error', message: 'No data' };
  var trade = JSON.parse(jsonStr);
  var sheet = getOrCreateSheet_('TradeLog');

  // Create header if empty
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, 9).setValues([[
      'Timestamp', 'Symbol', 'Entry', 'Exit', 'PnL%',
      'Outcome', 'BreathScore', 'HoldBars', 'SignalTime'
    ]]);
  }

  var row = [
    trade.timestamp || new Date().toISOString(),
    trade.symbol || '',
    trade.entry_price || 0,
    trade.exit_price || 0,
    trade.pnl_pct || 0,
    trade.outcome || '',       // TP, SL, MaxHold
    trade.breathing_score || 0,
    trade.hold_bars || 0,
    trade.signal_time || ''
  ];

  sheet.appendRow(row);

  // Update running coin stats
  updateCoinRunningStats_(trade.symbol, trade.pnl_pct, trade.outcome);

  return { status: 'success' };
}

function getTrades_(params) {
  var sheet = getOrCreateSheet_('TradeLog');
  if (sheet.getLastRow() <= 1) {
    return { status: 'success', data: [] };
  }

  var data = sheet.getDataRange().getValues();
  var headers = data[0];
  var trades = [];
  var limit = parseInt(params.limit) || 200;
  var coin = (params.coin || '').toUpperCase();

  // Read from bottom (newest first)
  for (var i = data.length - 1; i >= 1 && trades.length < limit; i--) {
    var row = {};
    for (var j = 0; j < headers.length; j++) {
      row[headers[j]] = data[i][j];
    }
    if (coin && row.Symbol.toUpperCase() !== coin) continue;
    trades.push(row);
  }

  return { status: 'success', data: trades };
}


// === COIN STATS ===

function initCoinStats_(jsonStr) {
  if (!jsonStr) return { status: 'error', message: 'No data' };
  var coins = JSON.parse(jsonStr);
  var sheet = getOrCreateSheet_('CoinStats');
  sheet.clear();

  // Header
  sheet.getRange(1, 1, 1, 13).setValues([[
    'Coin', 'Status', 'BT_WR', 'BT_PnL', 'BT_AvgPnL', 'BT_Kelly',
    'BT_MaxLS', 'BT_Trades', 'Live_Trades', 'Live_Wins',
    'Live_PnL', 'Live_WR', 'Live_AvgPnL'
  ]]);

  // Populate from backtest data
  var rows = [];
  for (var i = 0; i < coins.length; i++) {
    var c = coins[i];
    rows.push([
      c.coin, 'active',
      c.wr, c.pnl, c.avg_pnl, c.kelly,
      c.max_loss_streak, c.trades,
      0, 0, 0, 0, 0     // live stats start at 0
    ]);
  }

  if (rows.length > 0) {
    sheet.getRange(2, 1, rows.length, 13).setValues(rows);
  }

  return { status: 'success', count: rows.length };
}

function getCoinStats_() {
  var sheet = getOrCreateSheet_('CoinStats');
  if (sheet.getLastRow() <= 1) {
    return { status: 'success', data: [] };
  }

  var data = sheet.getDataRange().getValues();
  var headers = data[0];
  var coins = [];

  for (var i = 1; i < data.length; i++) {
    var row = {};
    for (var j = 0; j < headers.length; j++) {
      row[headers[j]] = data[i][j];
    }
    coins.push(row);
  }

  return { status: 'success', data: coins };
}

function updateCoinRunningStats_(symbol, pnlPct, outcome) {
  if (!symbol) return;
  var sheet = getOrCreateSheet_('CoinStats');
  if (sheet.getLastRow() <= 1) return;

  var coins = sheet.getRange(2, 1, sheet.getLastRow() - 1, 1).getValues();
  var rowIdx = -1;
  var sym = symbol.toLowerCase();

  for (var i = 0; i < coins.length; i++) {
    if (coins[i][0].toLowerCase() === sym) {
      rowIdx = i + 2;  // 1-indexed + header
      break;
    }
  }

  if (rowIdx === -1) return;

  // Read current live stats (cols I-M = 9-13)
  var range = sheet.getRange(rowIdx, 9, 1, 5);
  var vals = range.getValues()[0];
  var liveTrades = vals[0] + 1;
  var liveWins = vals[1] + (pnlPct > 0 ? 1 : 0);
  var livePnl = vals[2] + pnlPct;
  var liveWr = liveTrades > 0 ? (liveWins / liveTrades * 100) : 0;
  var liveAvg = liveTrades > 0 ? (livePnl / liveTrades) : 0;

  range.setValues([[liveTrades, liveWins, livePnl, liveWr, liveAvg]]);
}

function updateCoinStatus_(params) {
  var coin = (params.coin || '').toLowerCase();
  var status = params.status || '';
  if (!coin || !status) {
    return { status: 'error', message: 'Need coin and status' };
  }

  var sheet = getOrCreateSheet_('CoinStats');
  if (sheet.getLastRow() <= 1) {
    return { status: 'error', message: 'No coin data' };
  }

  var coins = sheet.getRange(2, 1, sheet.getLastRow() - 1, 1).getValues();
  for (var i = 0; i < coins.length; i++) {
    if (coins[i][0].toLowerCase() === coin) {
      sheet.getRange(i + 2, 2).setValue(status);
      return { status: 'success' };
    }
  }

  return { status: 'error', message: 'Coin not found' };
}


// === HELPERS ===

function getOrCreateSheet_(name) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
  }
  return sheet;
}


// === SETUP ===

function setupSheets() {
  getOrCreateSheet_('BotState');
  getOrCreateSheet_('TradeLog');
  getOrCreateSheet_('CoinStats');
  Logger.log('All sheets created');
}
