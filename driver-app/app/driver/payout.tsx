import React, { useState, useEffect } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    Platform,
    TextInput,
    Alert,
    ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useDriverStore } from '../../store/driverStore';
import SpinrConfig from '@shared/config/spinr.config';

const THEME = SpinrConfig.theme.colors;
const COLORS = {
    primary: THEME.background,
    accent: THEME.primary,
    accentDim: THEME.primaryDark,
    surface: THEME.surface,
    surfaceLight: THEME.surfaceLight,
    text: THEME.text,
    textDim: THEME.textDim,
    success: THEME.success,
    gold: '#FFD700',
    orange: '#FF9500',
    danger: THEME.error,
    border: THEME.border,
};

export default function PayoutScreen() {
    const router = useRouter();
    const {
        driverBalance,
        hasBankAccount,
        bankAccount,
        fetchDriverBalance,
        fetchBankAccount,
        setBankAccount,
        deleteBankAccount,
        requestPayout,
        isLoading,
        error,
        clearError,
    } = useDriverStore();

    const [payoutAmount, setPayoutAmount] = useState('');
    const [showAddBank, setShowAddBank] = useState(false);

    // Bank account form
    const [bankName, setBankName] = useState('');
    const [institutionNumber, setInstitutionNumber] = useState('');
    const [transitNumber, setTransitNumber] = useState('');
    const [accountNumber, setAccountNumber] = useState('');
    const [accountHolderName, setAccountHolderName] = useState('');
    const [accountType, setAccountType] = useState('checking');

    useEffect(() => {
        fetchDriverBalance();
        fetchBankAccount();
    }, []);

    useEffect(() => {
        if (error) {
            Alert.alert('Error', error);
            clearError();
        }
    }, [error]);

    const handleAddBankAccount = async () => {
        if (!bankName || !institutionNumber || !transitNumber || !accountNumber || !accountHolderName) {
            Alert.alert('Error', 'Please fill in all fields');
            return;
        }
        if (institutionNumber.length !== 3) {
            Alert.alert('Error', 'Institution number must be 3 digits');
            return;
        }
        if (transitNumber.length !== 5) {
            Alert.alert('Error', 'Transit number must be 5 digits');
            return;
        }
        if (accountNumber.length < 7 || accountNumber.length > 12) {
            Alert.alert('Error', 'Account number must be 7-12 digits');
            return;
        }

        const success = await setBankAccount({
            bank_name: bankName,
            institution_number: institutionNumber,
            transit_number: transitNumber,
            account_number: accountNumber,
            account_holder_name: accountHolderName,
            account_type: accountType,
            is_verified: false,
        });

        if (success) {
            setShowAddBank(false);
            resetForm();
            Alert.alert('Success', 'Bank account added successfully');
        }
    };

    const handleDeleteBankAccount = () => {
        Alert.alert(
            'Delete Bank Account',
            'Are you sure you want to remove this bank account?',
            [
                { text: 'Cancel', style: 'cancel' },
                { text: 'Delete', style: 'destructive', onPress: deleteBankAccount },
            ]
        );
    };

    const handleRequestPayout = async () => {
        const amount = parseFloat(payoutAmount);
        if (isNaN(amount) || amount <= 0) {
            Alert.alert('Error', 'Please enter a valid amount');
            return;
        }
        if (amount < 10) {
            Alert.alert('Error', 'Minimum payout amount is $10');
            return;
        }
        if (driverBalance && amount > driverBalance.available_balance) {
            Alert.alert('Error', `Insufficient balance. Available: $${driverBalance.available_balance.toFixed(2)}`);
            return;
        }

        const result = await requestPayout(amount);
        if (result.success) {
            setPayoutAmount('');
            Alert.alert('Success', 'Payout request submitted successfully');
        }
    };

    const resetForm = () => {
        setBankName('');
        setInstitutionNumber('');
        setTransitNumber('');
        setAccountNumber('');
        setAccountHolderName('');
        setAccountType('checking');
    };

    const formatCurrency = (amount: number) => `$${amount.toFixed(2)}`;

    return (
        <View style={styles.container}>
            {/* Header */}
            <LinearGradient colors={[COLORS.surface, COLORS.primary]} style={styles.header}>
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={COLORS.text} />
                    </TouchableOpacity>
                    <Text style={styles.headerTitle}>Payouts</Text>
                    <TouchableOpacity onPress={() => router.push('/(driver)/payout-history' as any)}>
                        <Ionicons name="time" size={22} color={COLORS.text} />
                    </TouchableOpacity>
                </View>
            </LinearGradient>

            <ScrollView contentContainerStyle={{ paddingBottom: 100 }} showsVerticalScrollIndicator={false}>
                {/* Balance Card */}
                <View style={styles.balanceCard}>
                    <Text style={styles.balanceLabel}>AVAILABLE BALANCE</Text>
                    <Text style={styles.balanceAmount}>
                        {driverBalance ? formatCurrency(driverBalance.available_balance) : '--'}
                    </Text>

                    <View style={styles.balanceDetails}>
                        <View style={styles.balanceItem}>
                            <Text style={styles.balanceItemLabel}>Total Earnings</Text>
                            <Text style={styles.balanceItemValue}>
                                {driverBalance ? formatCurrency(driverBalance.total_earnings) : '--'}
                            </Text>
                        </View>
                        <View style={styles.balanceDivider} />
                        <View style={styles.balanceItem}>
                            <Text style={styles.balanceItemLabel}>Pending</Text>
                            <Text style={styles.balanceItemValue}>
                                {driverBalance ? formatCurrency(driverBalance.pending_payouts) : '--'}
                            </Text>
                        </View>
                        <View style={styles.balanceDivider} />
                        <View style={styles.balanceItem}>
                            <Text style={styles.balanceItemLabel}>Paid Out</Text>
                            <Text style={styles.balanceItemValue}>
                                {driverBalance ? formatCurrency(driverBalance.total_paid_out) : '--'}
                            </Text>
                        </View>
                    </View>
                </View>

                {/* Payout Request */}
                {hasBankAccount && driverBalance && driverBalance.available_balance > 0 && (
                    <View style={styles.section}>
                        <Text style={styles.sectionTitle}>Request Payout</Text>
                        <View style={styles.payoutCard}>
                            <View style={styles.payoutInputRow}>
                                <Text style={styles.dollarSign}>$</Text>
                                <TextInput
                                    style={styles.payoutInput}
                                    placeholder="Amount"
                                    placeholderTextColor={COLORS.textDim}
                                    keyboardType="decimal-pad"
                                    value={payoutAmount}
                                    onChangeText={setPayoutAmount}
                                />
                                <TouchableOpacity
                                    style={[
                                        styles.payoutButton,
                                        (!payoutAmount || isLoading) && styles.payoutButtonDisabled,
                                    ]}
                                    onPress={handleRequestPayout}
                                    disabled={!payoutAmount || isLoading}
                                >
                                    {isLoading ? (
                                        <ActivityIndicator size="small" color="#fff" />
                                    ) : (
                                        <Text style={styles.payoutButtonText}>Request</Text>
                                    )}
                                </TouchableOpacity>
                            </View>
                            <TouchableOpacity
                                onPress={() => setPayoutAmount(driverBalance.available_balance.toString())}
                            >
                                <Text style={styles.maxAmount}>
                                    Available: {formatCurrency(driverBalance.available_balance)}
                                </Text>
                            </TouchableOpacity>
                        </View>
                    </View>
                )}

                {/* Bank Account Section */}
                <View style={styles.section}>
                    <View style={styles.sectionHeader}>
                        <Text style={styles.sectionTitle}>Bank Account</Text>
                        {!showAddBank && (
                            <TouchableOpacity onPress={() => setShowAddBank(true)}>
                                <Text style={styles.addLink}>
                                    {hasBankAccount ? 'Update' : 'Add'}
                                </Text>
                            </TouchableOpacity>
                        )}
                    </View>

                    {showAddBank ? (
                        <View style={styles.bankForm}>
                            <View style={styles.inputGroup}>
                                <Text style={styles.inputLabel}>Bank Name</Text>
                                <TextInput
                                    style={styles.textInput}
                                    placeholder="e.g., Royal Bank of Canada"
                                    placeholderTextColor={COLORS.textDim}
                                    value={bankName}
                                    onChangeText={setBankName}
                                />
                            </View>

                            <View style={styles.inputRow}>
                                <View style={[styles.inputGroup, { flex: 1 }]}>
                                    <Text style={styles.inputLabel}>Institution #</Text>
                                    <TextInput
                                        style={styles.textInput}
                                        placeholder="3 digits"
                                        placeholderTextColor={COLORS.textDim}
                                        keyboardType="number-pad"
                                        maxLength={3}
                                        value={institutionNumber}
                                        onChangeText={setInstitutionNumber}
                                    />
                                </View>
                                <View style={[styles.inputGroup, { flex: 1 }]}>
                                    <Text style={styles.inputLabel}>Transit #</Text>
                                    <TextInput
                                        style={styles.textInput}
                                        placeholder="5 digits"
                                        placeholderTextColor={COLORS.textDim}
                                        keyboardType="number-pad"
                                        maxLength={5}
                                        value={transitNumber}
                                        onChangeText={setTransitNumber}
                                    />
                                </View>
                            </View>

                            <View style={styles.inputGroup}>
                                <Text style={styles.inputLabel}>Account Number</Text>
                                <TextInput
                                    style={styles.textInput}
                                    placeholder="7-12 digits"
                                    placeholderTextColor={COLORS.textDim}
                                    keyboardType="number-pad"
                                    value={accountNumber}
                                    onChangeText={setAccountNumber}
                                />
                            </View>

                            <View style={styles.inputGroup}>
                                <Text style={styles.inputLabel}>Account Holder Name</Text>
                                <TextInput
                                    style={styles.textInput}
                                    placeholder="Name as shown on account"
                                    placeholderTextColor={COLORS.textDim}
                                    value={accountHolderName}
                                    onChangeText={setAccountHolderName}
                                />
                            </View>

                            <View style={styles.inputGroup}>
                                <Text style={styles.inputLabel}>Account Type</Text>
                                <View style={styles.typeSelector}>
                                    <TouchableOpacity
                                        style={[
                                            styles.typeOption,
                                            accountType === 'checking' && styles.typeOptionActive,
                                        ]}
                                        onPress={() => setAccountType('checking')}
                                    >
                                        <Text
                                            style={[
                                                styles.typeOptionText,
                                                accountType === 'checking' && styles.typeOptionTextActive,
                                            ]}
                                        >
                                            Checking
                                        </Text>
                                    </TouchableOpacity>
                                    <TouchableOpacity
                                        style={[
                                            styles.typeOption,
                                            accountType === 'savings' && styles.typeOptionActive,
                                        ]}
                                        onPress={() => setAccountType('savings')}
                                    >
                                        <Text
                                            style={[
                                                styles.typeOptionText,
                                                accountType === 'savings' && styles.typeOptionTextActive,
                                            ]}
                                        >
                                            Savings
                                        </Text>
                                    </TouchableOpacity>
                                </View>
                            </View>

                            <View style={styles.formButtons}>
                                {hasBankAccount && (
                                    <TouchableOpacity
                                        style={styles.deleteButton}
                                        onPress={handleDeleteBankAccount}
                                    >
                                        <Text style={styles.deleteButtonText}>Remove Account</Text>
                                    </TouchableOpacity>
                                )}
                                <TouchableOpacity
                                    style={styles.saveButton}
                                    onPress={handleAddBankAccount}
                                    disabled={isLoading}
                                >
                                    {isLoading ? (
                                        <ActivityIndicator size="small" color="#fff" />
                                    ) : (
                                        <Text style={styles.saveButtonText}>Save Account</Text>
                                    )}
                                </TouchableOpacity>
                            </View>

                            <TouchableOpacity
                                style={styles.cancelButton}
                                onPress={() => {
                                    setShowAddBank(false);
                                    resetForm();
                                }}
                            >
                                <Text style={styles.cancelButtonText}>Cancel</Text>
                            </TouchableOpacity>
                        </View>
                    ) : hasBankAccount ? (
                        <View style={styles.bankCard}>
                            <View style={styles.bankIcon}>
                                <Ionicons name="card" size={24} color={COLORS.accent} />
                            </View>
                            <View style={styles.bankInfo}>
                                <Text style={styles.bankName}>{bankAccount?.bank_name}</Text>
                                <Text style={styles.bankAccount}>
                                    {bankAccount?.account_type === 'checking' ? 'Checking' : 'Savings'} ••••{' '}
                                    {bankAccount?.account_number?.slice(-4)}
                                </Text>
                                <Text style={styles.bankHolder}>{bankAccount?.account_holder_name}</Text>
                            </View>
                            {bankAccount?.is_verified && (
                                <View style={styles.verifiedBadge}>
                                    <Ionicons name="checkmark-circle" size={16} color={COLORS.success} />
                                </View>
                            )}
                        </View>
                    ) : (
                        <TouchableOpacity style={styles.addBankCard} onPress={() => setShowAddBank(true)}>
                            <Ionicons name="add-circle-outline" size={32} color={COLORS.accent} />
                            <Text style={styles.addBankText}>Add Bank Account</Text>
                            <Text style={styles.addBankSubtext}>
                                Required for receiving payouts
                            </Text>
                        </TouchableOpacity>
                    )}
                </View>

                {/* Info Note */}
                <View style={styles.infoNote}>
                    <Ionicons name="information-circle" size={20} color={COLORS.textDim} />
                    <Text style={styles.infoText}>
                        Payouts are processed within 2-3 business days. Minimum payout is $10.
                    </Text>
                </View>
            </ScrollView>
        </View>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, backgroundColor: COLORS.primary },
    header: {
        paddingTop: Platform.OS === 'ios' ? 55 : 35,
        paddingBottom: 12,
        paddingHorizontal: 16,
    },
    headerRow: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
    },
    backBtn: {
        width: 40,
        height: 40,
        borderRadius: 20,
        backgroundColor: COLORS.surfaceLight,
        justifyContent: 'center',
        alignItems: 'center',
    },
    headerTitle: { color: COLORS.text, fontSize: 20, fontWeight: '700' },

    balanceCard: {
        backgroundColor: COLORS.accent,
        marginHorizontal: 16,
        marginTop: 16,
        borderRadius: 20,
        padding: 24,
    },
    balanceLabel: {
        color: 'rgba(255,255,255,0.7)',
        fontSize: 11,
        letterSpacing: 1.5,
        fontWeight: '600',
        textAlign: 'center',
    },
    balanceAmount: {
        color: '#fff',
        fontSize: 48,
        fontWeight: '800',
        textAlign: 'center',
        marginVertical: 8,
    },
    balanceDetails: {
        flexDirection: 'row',
        marginTop: 16,
        paddingTop: 16,
        borderTopWidth: 1,
        borderTopColor: 'rgba(255,255,255,0.2)',
    },
    balanceItem: { flex: 1, alignItems: 'center' },
    balanceItemLabel: {
        color: 'rgba(255,255,255,0.7)',
        fontSize: 10,
        marginBottom: 4,
    },
    balanceItemValue: {
        color: '#fff',
        fontSize: 14,
        fontWeight: '600',
    },
    balanceDivider: {
        width: 1,
        backgroundColor: 'rgba(255,255,255,0.2)',
    },

    section: {
        paddingHorizontal: 16,
        marginTop: 24,
    },
    sectionHeader: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: 12,
    },
    sectionTitle: {
        color: COLORS.text,
        fontSize: 17,
        fontWeight: '700',
    },
    addLink: {
        color: COLORS.accent,
        fontSize: 14,
        fontWeight: '600',
    },

    payoutCard: {
        backgroundColor: COLORS.surface,
        borderRadius: 16,
        padding: 16,
    },
    payoutInputRow: {
        flexDirection: 'row',
        alignItems: 'center',
    },
    dollarSign: {
        fontSize: 24,
        fontWeight: '700',
        color: COLORS.text,
        marginRight: 4,
    },
    payoutInput: {
        flex: 1,
        fontSize: 24,
        fontWeight: '700',
        color: COLORS.text,
        paddingVertical: 8,
    },
    payoutButton: {
        backgroundColor: COLORS.accent,
        paddingHorizontal: 24,
        paddingVertical: 12,
        borderRadius: 12,
    },
    payoutButtonDisabled: {
        opacity: 0.5,
    },
    payoutButtonText: {
        color: '#fff',
        fontSize: 16,
        fontWeight: '700',
    },
    maxAmount: {
        color: COLORS.accent,
        fontSize: 13,
        marginTop: 8,
    },

    bankForm: {
        backgroundColor: COLORS.surface,
        borderRadius: 16,
        padding: 16,
    },
    inputGroup: {
        marginBottom: 16,
    },
    inputLabel: {
        color: COLORS.textDim,
        fontSize: 12,
        marginBottom: 6,
        fontWeight: '500',
    },
    textInput: {
        backgroundColor: COLORS.surfaceLight,
        borderRadius: 12,
        paddingHorizontal: 14,
        paddingVertical: 12,
        fontSize: 15,
        color: COLORS.text,
    },
    inputRow: {
        flexDirection: 'row',
        gap: 12,
    },
    typeSelector: {
        flexDirection: 'row',
        gap: 12,
    },
    typeOption: {
        flex: 1,
        paddingVertical: 12,
        borderRadius: 12,
        backgroundColor: COLORS.surfaceLight,
        alignItems: 'center',
    },
    typeOptionActive: {
        backgroundColor: COLORS.accent,
    },
    typeOptionText: {
        color: COLORS.text,
        fontSize: 14,
        fontWeight: '600',
    },
    typeOptionTextActive: {
        color: '#fff',
    },
    formButtons: {
        flexDirection: 'row',
        gap: 12,
        marginTop: 8,
    },
    deleteButton: {
        flex: 1,
        paddingVertical: 14,
        borderRadius: 12,
        backgroundColor: 'rgba(255,71,87,0.1)',
        alignItems: 'center',
    },
    deleteButtonText: {
        color: COLORS.danger,
        fontSize: 14,
        fontWeight: '600',
    },
    saveButton: {
        flex: 2,
        paddingVertical: 14,
        borderRadius: 12,
        backgroundColor: COLORS.accent,
        alignItems: 'center',
    },
    saveButtonText: {
        color: '#fff',
        fontSize: 14,
        fontWeight: '600',
    },
    cancelButton: {
        alignItems: 'center',
        marginTop: 12,
        padding: 8,
    },
    cancelButtonText: {
        color: COLORS.textDim,
        fontSize: 14,
    },

    bankCard: {
        backgroundColor: COLORS.surface,
        borderRadius: 16,
        padding: 16,
        flexDirection: 'row',
        alignItems: 'center',
    },
    bankIcon: {
        width: 48,
        height: 48,
        borderRadius: 12,
        backgroundColor: 'rgba(0,212,170,0.1)',
        justifyContent: 'center',
        alignItems: 'center',
        marginRight: 14,
    },
    bankInfo: { flex: 1 },
    bankName: {
        color: COLORS.text,
        fontSize: 16,
        fontWeight: '600',
    },
    bankAccount: {
        color: COLORS.textDim,
        fontSize: 13,
        marginTop: 2,
    },
    bankHolder: {
        color: COLORS.textDim,
        fontSize: 12,
        marginTop: 2,
    },
    verifiedBadge: {
        marginLeft: 8,
    },

    addBankCard: {
        backgroundColor: COLORS.surface,
        borderRadius: 16,
        padding: 24,
        alignItems: 'center',
        borderWidth: 2,
        borderColor: COLORS.accent,
        borderStyle: 'dashed',
    },
    addBankText: {
        color: COLORS.accent,
        fontSize: 16,
        fontWeight: '600',
        marginTop: 8,
    },
    addBankSubtext: {
        color: COLORS.textDim,
        fontSize: 13,
        marginTop: 4,
    },

    infoNote: {
        flexDirection: 'row',
        alignItems: 'flex-start',
        marginHorizontal: 16,
        marginTop: 24,
        padding: 14,
        backgroundColor: COLORS.surface,
        borderRadius: 12,
        gap: 8,
    },
    infoText: {
        flex: 1,
        color: COLORS.textDim,
        fontSize: 13,
        lineHeight: 18,
    },
});
