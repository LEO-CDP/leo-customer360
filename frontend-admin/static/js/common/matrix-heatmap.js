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
        this.colorTheme = config.colorTheme || [
            { min: 0, color: '#ebedf0' },
            { min: 1, color: '#9be9a8' },
            { min: 4, color: '#40c463' },
            { min: 7, color: '#30a14e' },
            { min: 10, color: '#216e39' }
        ];

        // Transform the provided raw data into the strict format expected by the Chart.js Matrix plugin
        this.chartData = this._transformData(config.data || []);

        // Build the chart
        this._initChart();
    }

    /**
     * Transforms standard {date, count} objects into the matrix {x, y, v, d} format
     * @private
     */
    _transformData(rawData) {
        return rawData.map(item => {
            const dateObj = new Date(item.date);
            return {
                x: item.date,                 // X-axis mapping: ISO Date string
                y: dateObj.getDay(),          // Y-axis mapping: Day of week (0-6)
                v: item.count,                // Value mapping: The metric determining color
                d: item.date                  // Utility mapping: Raw date for tooltips
            };
        });
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
                    
                    // Dynamically calculate block width to fit exactly 53 weeks across the canvas
                    width: (context) => {
                        const chartArea = context.chart.chartArea;
                        if (!chartArea) return 0;
                        return (chartArea.right - chartArea.left) / 53 - 2;
                    },
                    
                    // Dynamically calculate block height to fit exactly 7 days vertically
                    height: (context) => {
                        const chartArea = context.chart.chartArea;
                        if (!chartArea) return 0;
                        return (chartArea.bottom - chartArea.top) / 7 - 2;
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
                        type: 'time',
                        time: {
                            unit: 'month',
                            round: 'week',
                            displayFormats: { month: 'MMM' }
                        },
                        ticks: { maxRotation: 0, autoSkip: true, font: { size: 11 } },
                        grid: { display: false, drawBorder: false }
                    },
                    y: {
                        type: 'linear',
                        position: 'left',
                        min: 0,
                        max: 6,
                        reverse: true, // Flips axis to put Sunday (0) at the top
                        ticks: {
                            maxRotation: 0,
                            stepSize: 1,
                            font: { size: 11 },
                            callback: (value) => {
                                const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
                                // Only display Monday, Wednesday, and Friday to match standard UI patterns
                                return [1, 3, 5].includes(value) ? days[value] : '';
                            }
                        },
                        grid: { display: false, drawBorder: false }
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
        this.chartData = this._transformData(newData);
        this.chartInstance.data.datasets[0].data = this.chartData;
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