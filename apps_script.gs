/**
 * RhythmFilter Apps Script Backend
 *
 * Sheet tabs:
 *   BotState   — A1=JSON blob, B1=timestamp
 *   TradeLog   — append-only trade history (row per trade)
 *   CoinStats  — per-coin running stats
 *   PnlHistory — daily P&L snapshots for chart
 *   Commands   — command queue (dashboard → bot)
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
    } else if (action === 'append_coins') {
      result = appendCoinStats_(e.parameter.data);
    } else if (action === 'clear_trades') {
      result = clearTrades_();
    } else if (action === 'snapshot_pnl') {
      result = snapshotPnl_(e.parameter);
    } else if (action === 'get_pnl_history') {
      result = getPnlHistory_();
    } else if (action === 'get_signal') {
      result = getLatestSignal_();
    } else if (action === 'ack_signal') {
      result = ackSignal_(e.parameter.id);
    } else if (action === 'queue_command') {
      result = queueCommand_(e.parameter);
    } else if (action === 'get_commands') {
      result = getPendingCommands_();
    } else if (action === 'ack_command') {
      result = ackCommand_(e.parameter);
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

// === POST RECEIVER (bot actions + TradingView webhook) ===

function doPost(e) {
  try {
    var body = e.postData.contents;
    var payload = JSON.parse(body);

    // Bot operations have an "action" field
    if (payload.action) {
      var action = payload.action.toLowerCase();
      var result = { status: 'error', message: 'Unknown action' };

      if (action === 'save_state') {
        result = saveState_(JSON.stringify(payload.data));
      } else if (action === 'log_trade') {
        result = logTrade_(JSON.stringify(payload.data));
      }

      return ContentService
        .createTextOutput(JSON.stringify(result))
        .setMimeType(ContentService.MimeType.JSON);
    }

    // No action field = TradingView webhook
    var signal = payload;
    var sheet = getOrCreateSheet_('Signals');
    if (sheet.getLastRow() === 0) {
      sheet.getRange(1, 1, 1, 6).setValues([[
        'Timestamp', 'Signal', 'Score', 'BuyZone',
        'SellZone', 'Acked'
      ]]);
    }

    sheet.appendRow([
      new Date().toISOString(),
      signal.signal || 'SHORT',
      signal.score || 0,
      signal.buyZone || 0,
      signal.sellZone || 0,
      'no'
    ]);

    return ContentService.createTextOutput('OK')
      .setMimeType(ContentService.MimeType.TEXT);
  } catch (err) {
    return ContentService.createTextOutput('ERROR: ' + err.toString())
      .setMimeType(ContentService.MimeType.TEXT);
  }
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


function clearTrades_() {
  var sheet = getOrCreateSheet_('TradeLog');
  if (sheet.getLastRow() > 1) {
    sheet.deleteRows(2, sheet.getLastRow() - 1);
  }
  return { status: 'success' };
}

// === COIN STATS ===

function initCoinStats_(jsonStr) {
  if (!jsonStr) return { status: 'error', message: 'No data' };
  var coins = JSON.parse(jsonStr);
  var sheet = getOrCreateSheet_('CoinStats');
  sheet.clear();

  // Header — 19 columns
  // A-H: backtest data, I-S: live running stats
  sheet.getRange(1, 1, 1, 19).setValues([[
    'Coin', 'Status', 'BT_WR', 'BT_PnL', 'BT_AvgPnL', 'BT_Kelly',
    'BT_MaxLS', 'BT_Trades',
    'Live_Trades', 'Live_Wins', 'Live_Losses',
    'Live_PnL', 'Live_WR', 'Live_AvgPnL',
    'Live_AvgWin', 'Live_AvgLoss', 'Live_Kelly',
    'Live_WinSum', 'Live_FirstTrade'
  ]]);

  // Populate from backtest data
  var rows = [];
  for (var i = 0; i < coins.length; i++) {
    var c = coins[i];
    rows.push([
      c.coin, 'active',
      c.wr, c.pnl, c.avg_pnl, c.kelly,
      c.max_loss_streak, c.trades,
      0, 0, 0,          // live trades, wins, losses
      0, 0, 0,          // live pnl, wr, avg pnl
      0, 0, 0,          // avg win, avg loss, kelly
      0,                 // win sum (for avg win calc)
      ''                 // first trade date
    ]);
  }

  if (rows.length > 0) {
    sheet.getRange(2, 1, rows.length, 19).setValues(rows);
  }

  return { status: 'success', count: rows.length };
}

function appendCoinStats_(jsonStr) {
  if (!jsonStr) return { status: 'error', message: 'No data' };
  var coins = JSON.parse(jsonStr);
  var sheet = getOrCreateSheet_('CoinStats');

  var rows = [];
  for (var i = 0; i < coins.length; i++) {
    var c = coins[i];
    rows.push([
      c.coin, 'active',
      c.wr, c.pnl, c.avg_pnl, c.kelly,
      c.max_loss_streak, c.trades,
      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ''
    ]);
  }

  if (rows.length > 0) {
    var lastRow = sheet.getLastRow();
    sheet.getRange(lastRow + 1, 1, rows.length, 19).setValues(rows);
  }

  return { status: 'success', count: rows.length, total: sheet.getLastRow() - 1 };
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

  // Read current live stats (cols I-S = 9-19)
  var range = sheet.getRange(rowIdx, 9, 1, 11);
  var v = range.getValues()[0];
  // v[0]=trades, v[1]=wins, v[2]=losses, v[3]=pnl,
  // v[4]=wr, v[5]=avgPnl, v[6]=avgWin, v[7]=avgLoss,
  // v[8]=kelly, v[9]=winSum, v[10]=firstTrade

  var isWin = pnlPct > 0;
  var liveTrades = v[0] + 1;
  var liveWins = v[1] + (isWin ? 1 : 0);
  var liveLosses = v[2] + (isWin ? 0 : 1);
  var livePnl = v[3] + pnlPct;
  var liveWr = liveTrades > 0 ? (liveWins / liveTrades * 100) : 0;
  var liveAvg = liveTrades > 0 ? (livePnl / liveTrades) : 0;

  // Track win sum for avg win calc
  var winSum = v[9] + (isWin ? pnlPct : 0);
  // Avg loss = (total pnl - win sum) / losses
  var lossSum = livePnl - winSum;

  var avgWin = liveWins > 0 ? (winSum / liveWins) : 0;
  var avgLoss = liveLosses > 0 ? Math.abs(lossSum / liveLosses) : 0;

  // Kelly criterion: f* = (b*p - q) / b
  // b = avgWin / avgLoss (reward/risk ratio)
  // p = win rate, q = 1-p
  var liveKelly = 0;
  if (avgLoss > 0 && liveTrades >= 5) {
    var b = avgWin / avgLoss;
    var p = liveWins / liveTrades;
    var q = 1 - p;
    liveKelly = (b * p - q) / b;
    if (liveKelly < 0) liveKelly = 0;
    if (liveKelly > 1) liveKelly = 1;
  }

  // Set first trade date if this is the first trade
  var firstTrade = v[10] || new Date().toISOString();

  range.setValues([[
    liveTrades, liveWins, liveLosses,
    livePnl, liveWr, liveAvg,
    avgWin, avgLoss, liveKelly, winSum, firstTrade
  ]]);
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


// === P&L HISTORY ===

function snapshotPnl_(params) {
  var pnl = parseFloat(params.pnl || '0');
  var trades = parseInt(params.trades || '0');
  var wr = parseFloat(params.wr || '0');

  var sheet = getOrCreateSheet_('PnlHistory');
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, 4).setValues([[
      'Date', 'PnL', 'Trades', 'WR'
    ]]);
  }

  // Today's date string (YYYY-MM-DD)
  var today = Utilities.formatDate(
    new Date(), 'America/New_York', 'yyyy-MM-dd');

  // Check if today already has a row — update it
  if (sheet.getLastRow() > 1) {
    var dates = sheet.getRange(2, 1,
      sheet.getLastRow() - 1, 1).getValues();
    for (var i = dates.length - 1; i >= 0; i--) {
      if (dates[i][0] === today) {
        var rowIdx = i + 2;
        sheet.getRange(rowIdx, 2, 1, 3)
          .setValues([[pnl, trades, wr]]);
        return { status: 'success', updated: true };
      }
    }
  }

  // New day — append
  sheet.appendRow([today, pnl, trades, wr]);
  return { status: 'success', updated: false };
}

function getPnlHistory_() {
  // Build daily cumulative P&L from TradeLog
  var tradeSheet = getOrCreateSheet_('TradeLog');
  if (tradeSheet.getLastRow() <= 1) {
    return { status: 'success', data: [] };
  }

  var data = tradeSheet.getDataRange().getValues();

  // Group trades by date
  var dailyMap = {};
  for (var i = 1; i < data.length; i++) {
    var ts = data[i][0]; // Timestamp
    var pnlPct = Number(data[i][4]) || 0; // PnL%

    var dateStr;
    if (ts instanceof Date) {
      dateStr = Utilities.formatDate(
        ts, 'America/New_York', 'yyyy-MM-dd');
    } else {
      dateStr = String(ts).substring(0, 10);
    }

    if (!dailyMap[dateStr]) {
      dailyMap[dateStr] = { pnl: 0, trades: 0, wins: 0 };
    }
    dailyMap[dateStr].pnl += pnlPct;
    dailyMap[dateStr].trades += 1;
    if (pnlPct > 0) dailyMap[dateStr].wins += 1;
  }

  var dates = Object.keys(dailyMap).sort();
  if (dates.length === 0) {
    return { status: 'success', data: [] };
  }

  // Fill every day from first trade to today
  var result = [];
  var cumPnl = 0;
  var cumTrades = 0;
  var cumWins = 0;

  var sp = dates[0].split('-');
  var start = new Date(
    parseInt(sp[0]), parseInt(sp[1]) - 1,
    parseInt(sp[2]), 12, 0, 0);
  var todayStr = Utilities.formatDate(
    new Date(), 'America/New_York', 'yyyy-MM-dd');
  var ep = todayStr.split('-');
  var end = new Date(
    parseInt(ep[0]), parseInt(ep[1]) - 1,
    parseInt(ep[2]), 12, 0, 0);

  for (var d = new Date(start); d <= end;
    d.setDate(d.getDate() + 1)) {
    var ds = Utilities.formatDate(
      d, 'America/New_York', 'yyyy-MM-dd');

    if (dailyMap[ds]) {
      cumPnl += dailyMap[ds].pnl;
      cumTrades += dailyMap[ds].trades;
      cumWins += dailyMap[ds].wins;
    }

    result.push({
      date: ds,
      pnl: Math.round(cumPnl * 10000) / 10000,
      trades: cumTrades,
      wr: cumTrades > 0
        ? Math.round(cumWins / cumTrades * 1000) / 10
        : 0
    });
  }

  return { status: 'success', data: result };
}

// === SIGNALS ===

function getLatestSignal_() {
  var sheet = getOrCreateSheet_('Signals');
  if (sheet.getLastRow() <= 1) {
    return { status: 'success', signal: null };
  }

  // Find most recent un-acked signal
  var data = sheet.getDataRange().getValues();
  for (var i = data.length - 1; i >= 1; i--) {
    if (data[i][5] !== 'yes') {
      return {
        status: 'success',
        signal: {
          id: i + 1,   // row number
          timestamp: data[i][0],
          type: data[i][1],
          score: data[i][2],
          buyZone: data[i][3],
          sellZone: data[i][4]
        }
      };
    }
  }

  return { status: 'success', signal: null };
}

function ackSignal_(rowId) {
  if (!rowId) return { status: 'error', message: 'No signal id' };
  var sheet = getOrCreateSheet_('Signals');
  var row = parseInt(rowId);
  if (row < 2 || row > sheet.getLastRow()) {
    return { status: 'error', message: 'Invalid row' };
  }
  sheet.getRange(row, 6).setValue('yes');
  return { status: 'success' };
}


// === COMMAND QUEUE (Dashboard → Bot) ===

function queueCommand_(params) {
  var cmd = params.cmd || '';
  if (!cmd) return { status: 'error', message: 'No cmd' };

  var sheet = getOrCreateSheet_('Commands');
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, 5).setValues([[
      'Timestamp', 'Command', 'Params', 'Status', 'Result'
    ]]);
  }

  sheet.appendRow([
    new Date().toISOString(),
    cmd,
    params.params || '',
    'pending',
    ''
  ]);

  return { status: 'success', message: 'Command queued: ' + cmd };
}

function getPendingCommands_() {
  var sheet = getOrCreateSheet_('Commands');
  if (sheet.getLastRow() <= 1) {
    return { status: 'success', commands: [] };
  }

  var data = sheet.getDataRange().getValues();
  var commands = [];

  for (var i = 1; i < data.length; i++) {
    if (data[i][3] === 'pending') {
      commands.push({
        row: i + 1,
        timestamp: data[i][0],
        command: data[i][1],
        params: data[i][2]
      });
    }
  }

  return { status: 'success', commands: commands };
}

function ackCommand_(params) {
  var row = parseInt(params.row);
  var result = params.result || 'done';
  if (!row) return { status: 'error', message: 'No row' };

  var sheet = getOrCreateSheet_('Commands');
  if (row < 2 || row > sheet.getLastRow()) {
    return { status: 'error', message: 'Invalid row' };
  }

  sheet.getRange(row, 4).setValue('done');
  sheet.getRange(row, 5).setValue(result);
  return { status: 'success' };
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
