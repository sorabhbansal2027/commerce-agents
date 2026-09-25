// Jest stub for lightning/navigation
const NavigationMixin = (Base) =>
    class extends Base {
        [NavigationMixin.Navigate] = jest.fn();
        [NavigationMixin.GenerateUrl] = jest.fn(() => Promise.resolve('/'));
    };

NavigationMixin.Navigate = Symbol('Navigate');
NavigationMixin.GenerateUrl = Symbol('GenerateUrl');

module.exports = { NavigationMixin };
