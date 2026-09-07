
// Utility functions for the frontend-admin app

// Show a toast notification using iziToast library
function showToast(message, kind) {
  iziToast.show({
    title: kind.charAt(0).toUpperCase() + kind.slice(1),
    message: message,
    position: "topRight",
    color: kind === "success" ? "green" : kind === "error" ? "red" : "blue",
  });
}

function formatCronSchedule(expression) {
  var raw = String(expression || "").trim();
  if (!raw) return "\u2014";

  var aliases = {
    "@hourly": "Every hour",
    "@daily": "Every day at 00:00",
    "@midnight": "Every day at 00:00",
    "@weekly": "Every Sunday at 00:00",
    "@monthly": "On day 1 of every month at 00:00",
    "@yearly": "Every January 1 at 00:00",
    "@annually": "Every January 1 at 00:00",
    "@reboot": "When the system starts"
  };
  if (aliases[raw.toLowerCase()]) return aliases[raw.toLowerCase()];

  var fields = raw.split(/\s+/);
  if (fields.length !== 5) return "Custom schedule";

  var minute = fields[0];
  var hour = fields[1];
  var dayOfMonth = fields[2];
  var month = fields[3];
  var dayOfWeek = fields[4];

  function isInteger(value, min, max) {
    return /^\d+$/.test(value) && Number(value) >= min && Number(value) <= max;
  }

  function formatTime(hourValue, minuteValue) {
    if (!isInteger(hourValue, 0, 23) || !isInteger(minuteValue, 0, 59)) return null;
    return String(hourValue).padStart(2, "0") + ":" + String(minuteValue).padStart(2, "0");
  }

  function formatDayOfWeek(field) {
    var dayNames = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
    var aliasesByName = {
      SUN: 0, MON: 1, TUE: 2, WED: 3, THU: 4, FRI: 5, SAT: 6
    };
    var tokens = field.toUpperCase().split(",");
    var days = [];

    for (var i = 0; i < tokens.length; i += 1) {
      var token = tokens[i];
      var range = token.split("-");
      if (range.length === 2) {
        var start = aliasesByName[range[0]];
        var end = aliasesByName[range[1]];
        if (start === undefined && isInteger(range[0], 0, 7)) start = Number(range[0]) % 7;
        if (end === undefined && isInteger(range[1], 0, 7)) end = Number(range[1]) % 7;
        if (start === undefined || end === undefined || start > end) return null;
        for (var day = start; day <= end; day += 1) days.push(dayNames[day]);
        continue;
      }

      var dayValue = aliasesByName[token];
      if (dayValue === undefined && isInteger(token, 0, 7)) dayValue = Number(token) % 7;
      if (dayValue === undefined) return null;
      days.push(dayNames[dayValue]);
    }

    var uniqueDays = days.filter(function (day, index) { return days.indexOf(day) === index; });
    if (!uniqueDays.length) return null;
    if (uniqueDays.length === 5 && uniqueDays.indexOf("Monday") !== -1 && uniqueDays.indexOf("Friday") !== -1 && uniqueDays.indexOf("Sunday") === -1 && uniqueDays.indexOf("Saturday") === -1) {
      return "weekday";
    }
    if (uniqueDays.length === 7) return "day";
    if (uniqueDays.length === 1) return uniqueDays[0];
    if (uniqueDays.length === 2) return uniqueDays.join(" and ");
    return uniqueDays.slice(0, -1).join(", ") + ", and " + uniqueDays[uniqueDays.length - 1];
  }

  function formatMonth(field) {
    var monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    var aliasesByName = {};
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"].forEach(function (name, index) {
      aliasesByName[name] = index + 1;
    });
    var tokens = field.toUpperCase().split(",");
    var months = tokens.map(function (token) {
      var value = aliasesByName[token];
      if (value === undefined && isInteger(token, 1, 12)) value = Number(token);
      return value === undefined ? null : monthNames[value - 1];
    });
    if (months.some(function (monthName) { return monthName === null; })) return null;
    if (months.length === 1) return months[0];
    return months.slice(0, -1).join(", ") + ", and " + months[months.length - 1];
  }

  if (raw === "* * * * *") return "Every minute";
  if (/^\*\/[1-9]\d*$/.test(minute) && hour === "*" && dayOfMonth === "*" && month === "*" && dayOfWeek === "*") {
    return "Every " + Number(minute.slice(2)) + " minutes";
  }
  if (isInteger(minute, 0, 59) && hour === "*" && dayOfMonth === "*" && month === "*" && dayOfWeek === "*") {
    return "Every hour at minute " + Number(minute);
  }
  if (/^\*\/[1-9]\d*$/.test(hour) && minute === "0" && dayOfMonth === "*" && month === "*" && dayOfWeek === "*") {
    return "Every " + Number(hour.slice(2)) + " hours";
  }

  var time = formatTime(hour, minute);
  if (!time) return "Custom schedule";

  if (dayOfMonth === "*" && month === "*" && dayOfWeek === "*") {
    return "Every day at " + time;
  }
  if (dayOfMonth === "*" && month === "*" && dayOfWeek.toUpperCase() !== "*") {
    var dayLabel = formatDayOfWeek(dayOfWeek);
    if (dayLabel) return "Every " + dayLabel + " at " + time;
  }
  if (isInteger(dayOfMonth, 1, 31) && month === "*" && dayOfWeek === "*") {
    return "On day " + Number(dayOfMonth) + " of every month at " + time;
  }
  if (isInteger(dayOfMonth, 1, 31) && month !== "*" && dayOfWeek === "*") {
    var monthLabel = formatMonth(month);
    if (monthLabel) return "Every " + monthLabel + " " + Number(dayOfMonth) + " at " + time;
  }

  return "At " + time + " on a custom schedule";
}
