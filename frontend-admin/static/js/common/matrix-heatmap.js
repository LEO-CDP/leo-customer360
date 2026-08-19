/**
 * MatrixHeatmap Class
 * A reusable Chart.js wrapper for generating GitHub-style activity heatmaps.
 */
class MatrixHeatmap {
    /**
     * @param {Object} config - Configuration object
     * @param {string} config.canvasId - The ID of the canvas element
     * @param {Array} config.data - Array of { date: 'YYYY-MM-DD', count: Number }
     * @param {string} config.entityName - Name for tooltips (e.g., "events", "profiles")
     * @param {Array} config.colorTheme - Array of color stops for the heatmap
     */
    constructor(config) {
        this.canvasId = config.canvasId;
        this.entityName = config.entityName || 'items';
        this.ctx = document.getElementById(this.canvasId).getContext('2d');
        this.chartInstance = null;

        // Default to a standard green progression theme if none is provided
        var defaultTheme = config.colorTheme || [
            { min: 0, color: '#ebedf0' },
            { min: 1, color: '#9be9a8' },
            { min: 4, color: '#40c463' },
            { min: 7, color: '#30a14e' },
            { min: 10, color: '#216e39' }
        ];
        // Sort ascending by threshold so _getColorForValue's backward scan is
        // correct even if the caller supplies stops out of order (matters most
        // for auto-generated, quantile-based themes on high-volume datasets).
        this.colorTheme = defaultTheme.slice().sort(function (a, b) { return a.min - b.min; });

        // weekCount/monthTicks are populated by _transformData
        this.weekCount = 1;
        this.monthTicks = [];
        this.chartData = this._transformData(config.data || []);

        this._initChart();
    }

    /**
     * Transforms {date, count} objects into GitHub-style matrix cells: each entry
     * is placed at (weekIndex, dayOfWeek) so every day in the same calendar week
     * lands in the same column, instead of being scattered along a continuous
     * date axis (which is what a Chart.js 'time' x-scale would do with raw dates).
     * @private
     */
    _transformData(rawData) {
        this.weekCount = 1;
        this.monthTicks = [];
        if (!rawData.length) return [];

        var sorted = rawData.slice().sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });

        // Grid starts on the Sunday on/before the first day, like GitHub's calendar.
        var first = new Date(sorted[0].date + 'T00:00:00Z');
        var gridStart = new Date(first);
        gridStart.setUTCDate(first.getUTCDate() - first.getUTCDay());

        var monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        var msPerDay = 24 * 60 * 60 * 1000;
        var maxWeek = 0;
        var lastMonth = -1;
        var monthTicks = [];

        var points = sorted.map(function (item) {
            var d = new Date(item.date + 'T00:00:00Z');
            var dayOfWeek = d.getUTCDay();
            var weekIndex = Math.round((d - gridStart) / (7 * msPerDay));
            maxWeek = Math.max(maxWeek, weekIndex);

            // Record the first week each calendar month appears in, for axis labels.
            var month = d.getUTCMonth();
            if (month !== lastMonth) {
                monthTicks.push({ value: weekIndex, label: monthNames[month] });
                lastMonth = month;
            }

            return { x: weekIndex, y: dayOfWeek, v: item.count, d: item.date };
        });

        this.weekCount = maxWeek + 1;
        this.monthTicks = monthTicks;
        return points;
    }

    /**
     * Evaluates the block color based on the numeric value and configured colorTheme
     * @private
     */
    _getColorForValue(value) {
        // Return base color for zero activity
        if (value === 0) return this.colorTheme[0].color;
        
        // Iterate backwards to find the highest threshold the value meets
        for (let i = this.colorTheme.length - 1; i >= 1; i--) {
            if (value >= this.colorTheme[i].min) {
                return this.colorTheme[i].color;
            }
        }
        
        // Fallback to the lightest shade if the value is > 0 but lower than the first threshold
        return this.colorTheme[1].color; 
    }

    /**
     * Bootstraps the Chart.js instance with the matrix configuration
     * @private
     */
    _initChart() {
        const self = this;

        this.chartInstance = new Chart(this.ctx, {
            type: 'matrix',
            data: {
                datasets: [{
                    label: this.entityName,
                    data: this.chartData,
                    
                    // Dynamic background color rendering
                    backgroundColor: (context) => {
                        const value = context.dataset.data[context.dataIndex]?.v || 0;
                        return this._getColorForValue(value);
                    },
                    
                    // Block styling (white borders to separate squares)
                    borderColor: '#ffffff',
                    borderWidth: 1,
                    borderRadius: 2,
                    
                    // Square blocks sized to the real week/day grid, capped so neither
                    // dimension outgrows the other.
                    width: (context) => {
                        const chartArea = context.chart.chartArea;
                        if (!chartArea) return 0;
                        const colWidth = (chartArea.right - chartArea.left) / self.weekCount - 2;
                        const rowHeight = (chartArea.bottom - chartArea.top) / 7 - 2;
                        return Math.max(0, Math.min(colWidth, rowHeight));
                    },
                    height: (context) => {
                        const chartArea = context.chart.chartArea;
                        if (!chartArea) return 0;
                        const colWidth = (chartArea.right - chartArea.left) / self.weekCount - 2;
                        const rowHeight = (chartArea.bottom - chartArea.top) / 7 - 2;
                        return Math.max(0, Math.min(colWidth, rowHeight));
                    }
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false, // Safely handles resizing thanks to wrapper CSS
                plugins: {
                    legend: { display: false }, // Disables top legend since heatmaps rely on visual scale
                    tooltip: {
                        displayColors: false, // Hides the little color box in the tooltip
                        callbacks: {
                            title: () => '', // Suppress the default title 
                            label: (context) => {
                                // Formats tooltip as "[X] profiles on [Date]"
                                const item = context.dataset.data[context.dataIndex];
                                const count = item.v;
                                const noun = count === 1 ? this.entityName.replace(/s$/, '') : this.entityName;
                                return `${count} ${noun} on ${item.d}`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        type: 'linear',
                        position: 'top', // GitHub shows month labels above the grid, not below
                        min: -0.5,
                        max: this.weekCount - 0.5,
                        // Only render ticks at the week columns where a new month starts,
                        // matching GitHub's "month label above its first column" layout.
                        afterBuildTicks: (axis) => {
                            axis.ticks = self.monthTicks.map((t) => ({ value: t.value }));
                        },
                        ticks: {
                            maxRotation: 0,
                            autoSkip: false,
                            padding: 4,
                            color: '#8b95a1',
                            font: { size: 11, weight: '400' },
                            callback: (value) => {
                                const tick = self.monthTicks.find((t) => t.value === value);
                                return tick ? tick.label : '';
                            }
                        },
                        grid: { display: false, drawBorder: false, drawTicks: false },
                        border: { display: false }
                    },
                    y: {
                        type: 'linear',
                        position: 'left',
                        min: -0.5,
                        max: 6.5,
                        reverse: true, // Flips axis to put Sunday (0) at the top
                        // A linear scale with -0.5/6.5 bounds auto-generates ticks at
                        // -0.5, 0.5, 1.5... (offset from the integer day indices), so the
                        // callback below never matched 1/3/5 and no weekday label rendered.
                        // Force exact integer ticks like the x-axis month ticks do.
                        afterBuildTicks: (axis) => {
                            axis.ticks = [0, 1, 2, 3, 4, 5, 6].map((v) => ({ value: v }));
                        },
                        ticks: {
                            maxRotation: 0,
                            padding: 4,
                            color: '#8b95a1',
                            font: { size: 11, weight: '400' },
                            callback: (value) => {
                                const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
                                // Only display Monday, Wednesday, and Friday to match standard UI patterns
                                return [1, 3, 5].includes(value) ? days[value] : '';
                            }
                        },
                        grid: { display: false, drawBorder: false, drawTicks: false },
                        border: { display: false }
                    }
                }
            }
        });
    }

    /**
     * Exposes a public method to update chart data dynamically (ideal for AJAX/fetch responses)
     * @param {Array} newData - Array of { date, count } objects
     */
    updateData(newData) {
        this.chartData = this._transformData(newData || []);
        this.chartInstance.data.datasets[0].data = this.chartData;
        this.chartInstance.options.scales.x.max = this.weekCount - 0.5;
        this.chartInstance.update();
    }

    /**
     * Safely destroys the chart instance to prevent memory leaks in Single Page Applications
     */
    destroy() {
        if (this.chartInstance) {
            this.chartInstance.destroy();
        }
    }
}