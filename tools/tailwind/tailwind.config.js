// Same theme as the in-browser config that base.html used to set (tailwind.config = {...}).
module.exports = {
  content: ["../../app/templates/**/*.html", "../../app/static/*.js", "../../app/**/*.py"],
  theme: { extend: { colors: { ink: '#0f1f3d', brand: { 50: '#eef3fb', 100: '#d9e4f5', 600: '#1f4e8c', 700: '#173d70', 800: '#112e56', 900: '#0c2242' } } } },
};
