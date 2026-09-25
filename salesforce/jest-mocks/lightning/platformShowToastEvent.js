// Jest stub for lightning/platformShowToastEvent
const ShowToastEventName = 'lightning__showtoast';

class ShowToastEvent extends CustomEvent {
    constructor({ title, message, variant }) {
        super(ShowToastEventName, { detail: { title, message, variant } });
    }
}

module.exports = { ShowToastEvent, ShowToastEventName };
