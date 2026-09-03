/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./index_1.html",
    "./blog/**/*.html",
    "./thank-you/**/*.html",
    "./strategy-of-the-week.html",
    "./strategy-detail.html",
    "./my-purchases.html",
    // These build markup as template strings at runtime, so the classes
    // they emit only exist here - not scanning them would drop those
    // classes from the build and silently unstyle whatever they render.
    "./assets/*.js",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
