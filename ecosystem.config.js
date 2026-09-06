module.exports = {
  apps: [
    {
      name: 'attendance-app',
      script: '/bin/gunicorn',
      args: '-w 2 --threads 4 -b 0.0.0.0:5000 --timeout 60 app:app',
      interpreter: 'none',
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '500M',
      env: {
        PORT: '5000',
        FLASK_DEBUG: 'false',
        SECRET_KEY: 'c12c129751a2f548895bbbc518289aef93a56b6125d44965a84ea5c90dcdac0c',
        DB_PATH: '/data/attendance.db',
        PYTHONUNBUFFERED: '1'
      }
    }
  ]
};
