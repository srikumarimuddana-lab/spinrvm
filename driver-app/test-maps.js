const https = require('https');
const key = 'AIzaSyC5i7lhtfXDoyYOB3KdyJtZ-CtKDzM5m9M';
const url = `https://maps.googleapis.com/maps/api/geocode/json?address=Saskatoon&key=${key}`;

https.get(url, (res) => {
    let data = '';
    res.on('data', (chunk) => data += chunk);
    res.on('end', () => {
        console.log(data);
    });
}).on('error', (err) => {
    console.log('Error: ' + err.message);
});
